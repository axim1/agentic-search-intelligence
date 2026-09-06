from __future__ import annotations

import asyncio
import operator
import time
from typing import Annotated, Any, Literal, TypedDict, cast
from urllib.parse import urlparse
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .domain import (
    AIToolArgs,
    AnalysisResult,
    Coverage,
    Insight,
    NodeEvent,
    Observation,
    PipelineError,
    PlannedCall,
    ProfileSnapshot,
    ProposedToolCall,
    QueryAnalysis,
    RecommendationDraft,
    Report,
    RetrievalResult,
    RunStatus,
    SerpToolArgs,
    SynthesisPayload,
    ToolName,
    VisibilityStatus,
    opportunity_score,
    recommendation_priority,
    utcnow,
)
from .providers import DataForSEOProvider
from .tools import PLANNER_TOOLS, validate_proposed_calls


class PipelineState(TypedDict, total=False):
    run_uuid: str
    trace_id: str
    run_kind: Literal["full", "recheck"]
    profile: ProfileSnapshot
    question: str
    max_queries: int
    started_at: Any
    deadline_monotonic: float
    proposed_calls: list[ProposedToolCall]
    plan: list[PlannedCall]
    plan_errors: list[PipelineError]
    planning_degraded: bool
    serp_results: list[RetrievalResult]
    ai_results: list[RetrievalResult]
    observations: list[Observation]
    normalization_errors: list[PipelineError]
    analysis: AnalysisResult | None
    synthesis_error: PipelineError | None
    report: Report
    node_events: Annotated[list[NodeEvent], operator.add]
    planner_token_usage: dict[str, int]
    analysis_token_usage: dict[str, int]


def _event(
    node: str,
    started: float,
    state: PipelineState,
    status: Literal["success", "failed", "skipped", "fallback"] = "success",
    input_summary: dict[str, Any] | None = None,
    retry_count: int = 0,
    **out: Any,
) -> list[NodeEvent]:
    return [
        NodeEvent(
            run_uuid=state["run_uuid"],
            trace_id=state["trace_id"],
            node=node,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            input_summary=input_summary or {},
            output_summary=out,
            retry_count=retry_count,
        )
    ]


def _hostname(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().encode("idna").decode().rstrip(".")
        return host.removeprefix("www.") or None
    except (UnicodeError, ValueError):
        return None


def _domain_matches(host: str | None, target: str) -> bool:
    if not host:
        return False
    normalized = target.lower().rstrip(".").removeprefix("www.")
    return host == normalized or host.endswith("." + normalized)


class PipelineGraph:
    def __init__(self, settings: Settings, provider: DataForSEOProvider):
        self.settings = settings
        self.provider = provider
        self._live_llm: ChatOpenAI | None = None
        if settings.llm_mode == "live":
            self._live_llm = ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.openai_api_key,
                timeout=15,
                temperature=0,
            )
        self.graph = self._build()

    async def query_planner(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        profile = state["profile"]
        if self._live_llm:
            model = self._live_llm.bind_tools(PLANNER_TOOLS)
            response = cast(
                AIMessage,
                await model.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                f"Plan at most {state['max_queries'] * 2} retrieval calls. "
                                "This question compares traditional search and AI visibility, "
                                "so make one search_google_serp call and one "
                                "query_chatgpt_visibility call for each selected intent. Choose "
                                "the arguments yourself. Do not ask the external model to "
                                "mention the target brand."
                            )
                        ),
                        HumanMessage(
                            content=f"Profile: {profile.model_dump_json()}\nQuestion: {state['question']}"
                        ),
                    ]
                ),
            )
            proposals = [
                ProposedToolCall(
                    call_id=str(call.get("id", uuid4())),
                    name=call["name"],
                    raw_arguments=call["args"],
                )
                for call in response.tool_calls
            ]
        else:
            base = state["question"].strip()
            intent = f"best {profile.industry}" if profile.industry else base
            proposals = []
            for keyword in [intent, f"{profile.name} alternatives"][: state["max_queries"]]:
                proposals.extend(
                    [
                        ProposedToolCall(
                            name=ToolName.SERP.value,
                            raw_arguments=SerpToolArgs(
                                keyword=keyword,
                                location_code=self.settings.default_location_code,
                                language_code=self.settings.default_language_code,
                            ).model_dump(),
                        ),
                        ProposedToolCall(
                            name=ToolName.AI.value,
                            raw_arguments=AIToolArgs(
                                user_prompt=f"Which tools are worth considering for: {keyword}?",
                                model_name=self.settings.dataforseo_chatgpt_model,
                                web_search_country_iso_code=self.settings.default_country_iso_code,
                            ).model_dump(),
                        ),
                    ]
                )
        update: dict[str, Any] = {
            "proposed_calls": proposals,
            "node_events": _event(
                "query_planner",
                started,
                state,
                input_summary={
                    "profile_uuid": profile.uuid,
                    "question_chars": len(state["question"]),
                    "max_queries": state["max_queries"],
                    "llm_mode": self.settings.llm_mode,
                },
                proposals=len(proposals),
            ),
        }
        if self._live_llm and response.usage_metadata:
            update["planner_token_usage"] = dict(response.usage_metadata)
        return update

    async def validate_plan(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        plan, errors = validate_proposed_calls(
            state.get("proposed_calls", []), self.settings.max_retrieval_calls
        )
        return {
            "plan": plan,
            "plan_errors": errors,
            "planning_degraded": bool(errors),
            "node_events": _event(
                "validate_plan",
                started,
                state,
                input_summary={"proposals": len(state.get("proposed_calls", []))},
                accepted=len(plan),
                rejected=len(errors),
            ),
        }

    async def planner_fallback(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        profile = state["profile"]
        args = SerpToolArgs(
            keyword=f"best {profile.industry}",
            location_code=self.settings.default_location_code,
            language_code=self.settings.default_language_code,
        )
        call = PlannedCall(
            ordinal=0,
            intent_group="fallback",
            query_text=args.keyword,
            tool_name=ToolName.SERP,
            arguments=args.model_dump(),
            origin="fallback",
        )
        return {
            "plan": [call],
            "planning_degraded": True,
            "node_events": _event(
                "planner_fallback",
                started,
                state,
                "fallback",
                input_summary={"rejected_calls": len(state.get("plan_errors", []))},
                calls=1,
            ),
        }

    async def recheck_seed(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        return {
            "node_events": _event(
                "recheck_seed",
                started,
                state,
                input_summary={"saved_calls": len(state.get("plan", []))},
                calls=len(state.get("plan", [])),
            )
        }

    async def dispatch(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        return {
            "node_events": _event(
                "dispatch",
                started,
                state,
                input_summary={"validated_calls": len(state["plan"])},
                calls=len(state["plan"]),
            )
        }

    async def _retrieve(
        self, state: PipelineState, tool_name: ToolName, node: str
    ) -> dict[str, Any]:
        started = time.perf_counter()
        calls = [call for call in state["plan"] if call.tool_name is tool_name]
        if not calls:
            key = "serp_results" if tool_name is ToolName.SERP else "ai_results"
            return {
                key: [],
                "node_events": _event(
                    node,
                    started,
                    state,
                    "skipped",
                    input_summary={"tool_name": tool_name.value, "calls": 0},
                    calls=0,
                ),
            }
        remaining = max(0.1, state["deadline_monotonic"] - time.monotonic())
        results = await asyncio.gather(*(self.provider.execute(call, remaining) for call in calls))
        key = "serp_results" if tool_name is ToolName.SERP else "ai_results"
        failures = sum(result.outcome == "failed" for result in results)
        retries = sum(max(0, result.attempt_count - 1) for result in results)
        return {
            key: results,
            "node_events": _event(
                node,
                started,
                state,
                "failed" if failures == len(results) else "success",
                input_summary={"tool_name": tool_name.value, "calls": len(calls)},
                retry_count=retries,
                calls=len(calls),
                failures=failures,
                api_attempts=sum(result.attempt_count for result in results),
            ),
        }

    async def serp_retrieval(self, state: PipelineState) -> dict[str, Any]:
        return await self._retrieve(state, ToolName.SERP, "serp_retrieval")

    async def ai_retrieval(self, state: PipelineState) -> dict[str, Any]:
        return await self._retrieve(state, ToolName.AI, "ai_retrieval")

    async def normalize(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        observations: list[Observation] = []
        errors: list[PipelineError] = []
        for result in state.get("serp_results", []) + state.get("ai_results", []):
            if result.outcome != "success" or not result.raw_payload:
                continue
            try:
                task_result = result.raw_payload["tasks"][0]["result"]
                items = task_result[0].get("items", []) if task_result else []
                if not items:
                    observations.append(
                        Observation(
                            query_uuid=result.query_uuid,
                            channel="organic" if result.tool_name is ToolName.SERP else "chatgpt",
                            record_type="empty",
                            source_task_id=result.provider_task_id,
                            provenance={
                                "mode": result.mode,
                                "evidence_available": result.tool_name is ToolName.SERP,
                            },
                        )
                    )
                for item in items:
                    if result.tool_name is ToolName.SERP and item.get("type") == "organic":
                        url = item.get("url")
                        observations.append(
                            Observation(
                                query_uuid=result.query_uuid,
                                channel="organic",
                                record_type="result",
                                title=item.get("title"),
                                url=url,
                                domain=_hostname(url),
                                organic_rank=item.get("rank_group"),
                                source_task_id=result.provider_task_id,
                                provenance={"mode": result.mode},
                            )
                        )
                    elif result.tool_name is ToolName.SERP and item.get("type") in {
                        "ai_overview",
                        "featured_snippet",
                    }:
                        url = item.get("url")
                        observations.append(
                            Observation(
                                query_uuid=result.query_uuid,
                                channel="ai_overview",
                                record_type="citation",
                                title=item.get("title"),
                                text=item.get("text"),
                                url=url,
                                domain=_hostname(url),
                                source_task_id=result.provider_task_id,
                                provenance={"mode": result.mode},
                            )
                        )
                    elif result.tool_name is ToolName.AI:
                        text = item.get("text") or item.get("message")
                        if text:
                            observations.append(
                                Observation(
                                    query_uuid=result.query_uuid,
                                    channel="chatgpt",
                                    record_type="mention",
                                    text=text,
                                    source_task_id=result.provider_task_id,
                                    provenance={
                                        "mode": result.mode,
                                        "citation_coverage": bool(item.get("citations")),
                                    },
                                )
                            )
                        for citation in item.get("citations", []):
                            url = citation.get("url")
                            observations.append(
                                Observation(
                                    query_uuid=result.query_uuid,
                                    channel="chatgpt",
                                    record_type="citation",
                                    title=citation.get("title"),
                                    url=url,
                                    domain=_hostname(url),
                                    source_task_id=result.provider_task_id,
                                    provenance={"mode": result.mode},
                                )
                            )
            except (KeyError, TypeError, IndexError) as exc:
                errors.append(
                    PipelineError(
                        code="normalization_error",
                        stage="normalization",
                        message=type(exc).__name__,
                        query_uuid=result.query_uuid,
                    )
                )
        return {
            "observations": observations,
            "normalization_errors": errors,
            "node_events": _event(
                "normalize",
                started,
                state,
                input_summary={
                    "retrieval_results": len(
                        state.get("serp_results", []) + state.get("ai_results", [])
                    )
                },
                records=len(observations),
                errors=len(errors),
            ),
        }

    def _deterministic_analysis(self, state: PipelineState) -> AnalysisResult:
        profile = state["profile"]
        all_results = state.get("serp_results", []) + state.get("ai_results", [])
        query_results: list[QueryAnalysis] = []
        insights: list[Insight] = []
        recommendations: list[RecommendationDraft] = []
        for call in state["plan"]:
            obs = [
                item for item in state.get("observations", []) if item.query_uuid == call.query_uuid
            ]
            retrieval = next(
                (item for item in all_results if item.query_uuid == call.query_uuid), None
            )
            visible = any(_domain_matches(item.domain, profile.domain) for item in obs)
            brand_mentioned = any(profile.name.lower() in (item.text or "").lower() for item in obs)
            position = min(
                (
                    item.organic_rank
                    for item in obs
                    if _domain_matches(item.domain, profile.domain) and item.organic_rank
                ),
                default=None,
            )
            adequate = call.tool_name is ToolName.SERP or any(
                item.record_type == "citation" for item in obs
            )
            status = (
                VisibilityStatus.UNKNOWN
                if not retrieval or retrieval.outcome != "success" or not adequate
                else (VisibilityStatus.VISIBLE if visible else VisibilityStatus.NOT_VISIBLE)
            )
            relevance = 1.0
            score, available = opportunity_score(relevance, status, position)
            qa = QueryAnalysis(
                query_uuid=call.query_uuid,
                visibility_status=status,
                domain_visible=visible,
                brand_mentioned=brand_mentioned,
                visibility_position=position,
                relevance=relevance,
                opportunity_score=score,
                score_available=available,
            )
            query_results.append(qa)
            evidence_ids = [item.uuid for item in obs if item.record_type != "empty"]
            if available:
                detail = f"{profile.name} is {status.value.replace('_', ' ')} in the sampled {call.tool_name.value} evidence."
                insights.append(
                    Insight(
                        title=f"Visibility for {call.query_text}",
                        detail=detail,
                        query_uuid=call.query_uuid,
                        evidence_ids=evidence_ids,
                        relevance=relevance,
                        opportunity_score=score,
                    )
                )
            if status is VisibilityStatus.NOT_VISIBLE:
                recommendations.append(
                    RecommendationDraft(
                        target_query_uuid=call.query_uuid,
                        content_type="blog_post",
                        title=f"Create a resource for: {call.query_text}",
                        rationale="The target domain was not present in the successfully sampled evidence.",
                        target_keywords=[call.query_text],
                        priority=recommendation_priority(score),
                        evidence_ids=evidence_ids,
                    )
                )
        return AnalysisResult(
            queries=query_results,
            insights=insights[:5],
            recommendations=recommendations[:5],
            synthesis_mode="mock" if self.settings.llm_mode == "mock" else "deterministic_fallback",
            limitations=[
                "Results are point-in-time samples.",
                "Search volume and keyword difficulty are unavailable in the selected endpoints.",
            ],
        )

    async def analysis(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        baseline = self._deterministic_analysis(state)
        if not self._live_llm:
            return {
                "analysis": baseline,
                "synthesis_error": None,
                "node_events": _event(
                    "analysis",
                    started,
                    state,
                    input_summary={
                        "planned_calls": len(state["plan"]),
                        "evidence_records": len(state.get("observations", [])),
                    },
                    insights=len(baseline.insights),
                ),
            }
        try:
            prompt = f"Produce at most five grounded insights and recommendations. Use only these UUIDs and evidence: {baseline.model_dump_json()}"
            structured = cast(
                dict[str, Any],
                await self._live_llm.with_structured_output(
                    SynthesisPayload, include_raw=True
                ).ainvoke(prompt),
            )
            synthesis = cast(SynthesisPayload, structured["parsed"])
            raw_message = cast(AIMessage, structured["raw"])
            queries_by_id = {q.query_uuid: q for q in baseline.queries}
            query_ids = set(queries_by_id)
            evidence_ids = {o.uuid for o in state.get("observations", [])}
            if any(
                i.query_uuid not in query_ids or not set(i.evidence_ids) <= evidence_ids
                for i in synthesis.insights
            ):
                raise ValueError("synthesis referenced unknown evidence")
            if any(
                r.target_query_uuid not in query_ids or not set(r.evidence_ids) <= evidence_ids
                for r in synthesis.recommendations
            ):
                raise ValueError("synthesis referenced unknown query or evidence")
            for insight in synthesis.insights:
                query = queries_by_id[insight.query_uuid]
                insight.relevance = query.relevance
                insight.opportunity_score = query.opportunity_score
            for recommendation in synthesis.recommendations:
                query = queries_by_id[recommendation.target_query_uuid]
                recommendation.priority = (
                    recommendation_priority(query.opportunity_score)
                    if query.score_available
                    else "low"
                )
            baseline.insights = synthesis.insights
            baseline.recommendations = synthesis.recommendations
            baseline.synthesis_mode = "llm"
            update: dict[str, Any] = {
                "analysis": baseline,
                "synthesis_error": None,
                "node_events": _event(
                    "analysis",
                    started,
                    state,
                    input_summary={
                        "planned_calls": len(state["plan"]),
                        "evidence_records": len(state.get("observations", [])),
                    },
                    insights=len(baseline.insights),
                ),
            }
            if raw_message.usage_metadata:
                update["analysis_token_usage"] = dict(raw_message.usage_metadata)
            return update
        except Exception as exc:
            error = PipelineError(
                code="synthesis_failed", stage="analysis", message=type(exc).__name__
            )
            return {
                "analysis": None,
                "synthesis_error": error,
                "node_events": _event(
                    "analysis",
                    started,
                    state,
                    "failed",
                    input_summary={
                        "planned_calls": len(state["plan"]),
                        "evidence_records": len(state.get("observations", [])),
                    },
                    error_code=error.code,
                ),
            }

    async def analysis_fallback(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        result = self._deterministic_analysis(state)
        result.synthesis_mode = "deterministic_fallback"
        return {
            "analysis": result,
            "node_events": _event(
                "analysis_fallback",
                started,
                state,
                "fallback",
                input_summary={"synthesis_failed": state.get("synthesis_error") is not None},
                insights=len(result.insights),
            ),
        }

    async def report(self, state: PipelineState) -> dict[str, Any]:
        started = time.perf_counter()
        results = state.get("serp_results", []) + state.get("ai_results", [])
        errors = list(state.get("plan_errors", [])) + list(state.get("normalization_errors", []))
        errors.extend(item.error for item in results if item.error)
        if state.get("synthesis_error"):
            errors.append(state["synthesis_error"])  # type: ignore[arg-type]
        successes = sum(item.outcome == "success" for item in results)
        failures = sum(item.outcome == "failed" for item in results)
        degraded = state.get("planning_degraded", False) or state.get("synthesis_error") is not None
        status = (
            RunStatus.FAILED
            if successes == 0
            else (
                RunStatus.PARTIAL
                if failures or degraded or state.get("normalization_errors")
                else RunStatus.COMPLETED
            )
        )
        analysis = state.get("analysis") or AnalysisResult(
            queries=[],
            insights=[],
            recommendations=[],
            synthesis_mode="deterministic_fallback",
            limitations=["No usable retrieval evidence was available."],
        )
        coverage = Coverage(
            planned_calls=len(state.get("plan", [])),
            attempted_calls=len(results),
            successful_calls=successes,
            failed_calls=failures,
            rejected_tool_calls=len(state.get("plan_errors", [])),
            normalized_records=len(state.get("observations", [])),
            api_attempts=sum(item.attempt_count for item in results),
        )
        summary = f"Run {status.value}: {successes} of {len(results)} retrieval calls succeeded; {len(state.get('observations', []))} evidence records were normalized."
        planner_tokens = state.get("planner_token_usage")
        analysis_tokens = state.get("analysis_token_usage")
        usage_complete = bool(analysis_tokens) and (
            state["run_kind"] == "recheck" or bool(planner_tokens)
        )
        total_tokens = None
        if usage_complete:
            total_tokens = (planner_tokens or {}).get("total_tokens", 0) + (
                analysis_tokens or {}
            ).get("total_tokens", 0)
        report = Report(
            run_uuid=state["run_uuid"],
            profile_uuid=state["profile"].uuid,
            effective_question=state["question"],
            status=status,
            data_mode=self.settings.dataforseo_mode,
            llm_mode=self.settings.llm_mode,
            coverage=coverage,
            query_results=analysis.queries,
            insights=analysis.insights,
            recommendations=analysis.recommendations,
            errors=errors,
            limitations=analysis.limitations,
            summary=summary,
            started_at=state["started_at"],
            finished_at=utcnow(),
            total_tokens_used=total_tokens,
            token_usage_available=usage_complete,
            trace_id=state["trace_id"],
        )
        return {
            "report": report,
            "node_events": _event(
                "report",
                started,
                state,
                input_summary={
                    "retrieval_results": len(results),
                    "pipeline_errors": len(errors),
                },
                status="success",
                run_status=status.value,
            ),
        }

    def _build(self):
        builder = StateGraph(PipelineState)
        for name in [
            "query_planner",
            "validate_plan",
            "planner_fallback",
            "recheck_seed",
            "dispatch",
            "serp_retrieval",
            "ai_retrieval",
            "normalize",
            "analysis",
            "analysis_fallback",
            "report",
        ]:
            builder.add_node(name, getattr(self, name))
        builder.add_conditional_edges(
            START, lambda s: s["run_kind"], {"full": "query_planner", "recheck": "recheck_seed"}
        )
        builder.add_edge("query_planner", "validate_plan")
        builder.add_conditional_edges(
            "validate_plan",
            lambda s: "valid" if s.get("plan") else "fallback",
            {"valid": "dispatch", "fallback": "planner_fallback"},
        )
        builder.add_edge("planner_fallback", "dispatch")
        builder.add_edge("recheck_seed", "dispatch")
        builder.add_edge("dispatch", "serp_retrieval")
        builder.add_edge("dispatch", "ai_retrieval")
        builder.add_edge(["serp_retrieval", "ai_retrieval"], "normalize")
        builder.add_conditional_edges(
            "normalize",
            lambda s: (
                "analyze"
                if any(
                    r.outcome == "success"
                    for r in s.get("serp_results", []) + s.get("ai_results", [])
                )
                else "report"
            ),
            {"analyze": "analysis", "report": "report"},
        )
        builder.add_conditional_edges(
            "analysis",
            lambda s: "fallback" if s.get("synthesis_error") else "report",
            {"fallback": "analysis_fallback", "report": "report"},
        )
        builder.add_edge("analysis_fallback", "report")
        builder.add_edge("report", END)
        return builder.compile()
