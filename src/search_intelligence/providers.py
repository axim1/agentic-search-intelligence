from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from .config import Settings
from .domain import PipelineError, PlannedCall, RetrievalResult, ToolName


class ProviderFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


class DataForSEOProvider:
    BASE_URL = "https://api.dataforseo.com"
    ENDPOINTS = {
        ToolName.SERP: "/v3/serp/google/organic/live/advanced",
        ToolName.AI: "/v3/ai_optimization/chat_gpt/llm_responses/live",
    }

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._attempts: dict[str, int] = {}
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout_seconds,
            read=settings.http_read_timeout_seconds,
            write=settings.http_write_timeout_seconds,
            pool=settings.http_pool_timeout_seconds,
        )
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.semaphore = asyncio.Semaphore(settings.dataforseo_max_concurrency)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def execute(self, call: PlannedCall, remaining_seconds: float) -> RetrievalResult:
        started = time.perf_counter()
        last: ProviderFailure | None = None
        attempts = 0
        for attempt in range(1, self.settings.max_api_attempts + 1):
            attempts = attempt
            try:
                async with self.semaphore:
                    payload = await asyncio.wait_for(
                        self._one_attempt(call), timeout=max(0.1, remaining_seconds)
                    )
                task = payload["tasks"][0]
                return RetrievalResult(
                    query_uuid=call.query_uuid,
                    tool_name=call.tool_name,
                    mode=self.settings.dataforseo_mode,
                    outcome="success",
                    raw_payload=payload,
                    provider_task_id=task.get("id"),
                    attempt_count=attempt,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            except TimeoutError:
                last = ProviderFailure("timeout", "Provider request timed out", True)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = ProviderFailure("network_error", type(exc).__name__, True)
            except ProviderFailure as exc:
                last = exc
            if not last.retryable or attempt >= self.settings.max_api_attempts:
                break
            delay = random.uniform(
                0,
                min(
                    self.settings.backoff_cap_seconds,
                    self.settings.backoff_base_seconds * 2 ** (attempt - 1),
                ),
            )
            if delay >= remaining_seconds:
                break
            await asyncio.sleep(delay)
            remaining_seconds -= delay
        assert last is not None
        error = PipelineError(
            code=last.code,
            stage="retrieval",
            message=last.message,
            retryable=last.retryable,
            query_uuid=call.query_uuid,
            attempt_count=attempts,
            provider_status=last.status,
        )
        return RetrievalResult(
            query_uuid=call.query_uuid,
            tool_name=call.tool_name,
            mode=self.settings.dataforseo_mode,
            outcome="failed",
            attempt_count=attempts,
            duration_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )

    async def _one_attempt(self, call: PlannedCall) -> dict[str, Any]:
        self._attempts[call.query_uuid] = self._attempts.get(call.query_uuid, 0) + 1
        if self.settings.dataforseo_mode == "mock":
            return self._mock(call, self._attempts[call.query_uuid])
        response = await self.client.post(
            self.BASE_URL + self.ENDPOINTS[call.tool_name],
            json=[call.arguments],
            auth=(self.settings.dataforseo_login or "", self.settings.dataforseo_password or ""),
        )
        if response.status_code == 429 or response.status_code >= 500:
            code, message, status = self._http_error_details(response)
            raise ProviderFailure(code, message, True, status)
        if response.status_code >= 400:
            code, message, status = self._http_error_details(response)
            raise ProviderFailure(code, message, False, status)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderFailure(
                "malformed_json", "DataForSEO returned malformed JSON", False
            ) from exc
        self._validate_envelope(payload)
        return payload

    @staticmethod
    def _http_error_details(response: httpx.Response) -> tuple[str, str, int]:
        provider_status: int | None = None
        provider_message: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                provider_status = payload.get("status_code")
                provider_message = payload.get("status_message")
        except ValueError:
            pass
        status = provider_status or response.status_code
        named_codes = {
            40100: "dataforseo_unauthorized",
            40104: "dataforseo_account_unverified",
            40200: "dataforseo_payment_required",
            40203: "dataforseo_cost_limit_exceeded",
        }
        fallback_code = "http_transient" if response.status_code >= 500 else "http_permanent"
        code = named_codes.get(status, fallback_code)
        message = provider_message or (
            f"DataForSEO HTTP request failed with status {response.status_code}"
        )
        return code, message[:500], status

    def _validate_envelope(self, payload: Any) -> None:
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("tasks"), list)
            or not payload["tasks"]
        ):
            raise ProviderFailure("invalid_envelope", "Missing DataForSEO task envelope", False)
        outer = payload.get("status_code")
        task = payload["tasks"][0]
        task_code = task.get("status_code") if isinstance(task, dict) else None
        if outer != 20000 or task_code != 20000:
            status = task_code or outer
            retryable = status in {40601, 40602, 50000, 50300}
            raise ProviderFailure(
                "provider_task_error", "DataForSEO task failed", retryable, status
            )
        if not isinstance(task.get("result"), list):
            raise ProviderFailure("invalid_result", "DataForSEO task result is missing", False)

    def _mock(self, call: PlannedCall, attempt: int) -> dict[str, Any]:
        scenario = self.settings.mock_scenario
        if scenario == "transient_then_success" and attempt == 1:
            raise ProviderFailure("rate_limited", "Simulated rate limit", True, 429)
        if scenario == "all_failed" or (
            scenario == "partial_failure" and call.tool_name is ToolName.AI
        ):
            raise ProviderFailure("simulated_outage", "Simulated provider outage", True, 503)
        domain = "surferseo.com"
        if call.tool_name is ToolName.SERP:
            keyword = call.arguments["keyword"]
            items = [
                {
                    "type": "organic",
                    "rank_group": 1,
                    "title": "Best tools",
                    "url": "https://example.com/best-tools",
                },
                {
                    "type": "organic",
                    "rank_group": 4,
                    "title": "Surfer",
                    "url": f"https://{domain}/features",
                },
            ]
            if "alternative" in keyword.lower() or "competitor" in keyword.lower():
                items = items[:1]
            result = [{"keyword": keyword, "items": items}]
        else:
            result = [
                {
                    "items": [
                        {
                            "type": "message",
                            "text": "Surfer SEO is one option among several content optimization platforms.",
                            "citations": [{"url": f"https://{domain}/", "title": "Surfer SEO"}],
                        }
                    ]
                }
            ]
        payload = {
            "status_code": 20000,
            "status_message": "Ok.",
            "tasks": [
                {
                    "id": f"mock-{call.query_uuid}",
                    "status_code": 20000,
                    "status_message": "Ok.",
                    "result": result,
                }
            ],
        }
        self._validate_envelope(payload)
        return payload
