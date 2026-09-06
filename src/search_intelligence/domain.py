from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolName(StrEnum):
    SERP = "search_google_serp"
    AI = "query_chatgpt_visibility"


class VisibilityStatus(StrEnum):
    VISIBLE = "visible"
    NOT_VISIBLE = "not_visible"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ProfileSnapshot(BaseModel):
    uuid: str
    name: str
    domain: str
    industry: str
    description: str
    competitors: list[str]


class SerpToolArgs(StrictModel):
    keyword: str = Field(min_length=1, max_length=700)
    location_code: int = Field(gt=0)
    language_code: str = Field(min_length=2, max_length=10)
    device: Literal["desktop", "mobile"] = "desktop"
    depth: int = Field(default=10, ge=1, le=10)


class AIToolArgs(StrictModel):
    user_prompt: str = Field(min_length=1, max_length=2000)
    model_name: str = Field(min_length=1, max_length=100)
    web_search: bool = True
    web_search_country_iso_code: str = Field(min_length=2, max_length=2)
    max_output_tokens: int = Field(default=500, ge=100, le=1000)


class ProposedToolCall(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    raw_arguments: dict[str, Any] | str
    parse_error: str | None = None


class PlannedCall(BaseModel):
    query_uuid: str = Field(default_factory=lambda: str(uuid4()))
    ordinal: int
    intent_group: str
    query_text: str
    tool_name: ToolName
    arguments: dict[str, Any]
    origin: Literal["llm", "fallback", "mock"]


class PipelineError(BaseModel):
    code: str
    stage: str
    message: str
    retryable: bool = False
    query_uuid: str | None = None
    attempt_count: int = 0
    provider_status: int | str | None = None


class RetrievalResult(BaseModel):
    query_uuid: str
    tool_name: ToolName
    mode: Literal["mock", "live"]
    outcome: Literal["success", "failed", "skipped"]
    raw_payload: dict[str, Any] | None = None
    provider_task_id: str | None = None
    attempt_count: int = 0
    duration_ms: float = 0
    retrieved_at: datetime = Field(default_factory=utcnow)
    error: PipelineError | None = None


class Observation(BaseModel):
    uuid: str = Field(default_factory=lambda: str(uuid4()))
    query_uuid: str
    channel: Literal["organic", "ai_overview", "chatgpt"]
    record_type: Literal["result", "citation", "mention", "empty"]
    title: str | None = None
    text: str | None = None
    url: str | None = None
    domain: str | None = None
    organic_rank: int | None = None
    source_task_id: str | None = None
    observed_at: datetime = Field(default_factory=utcnow)
    provenance: dict[str, Any] = Field(default_factory=dict)


class QueryAnalysis(BaseModel):
    query_uuid: str
    visibility_status: VisibilityStatus
    domain_visible: bool
    brand_mentioned: bool
    visibility_position: int | None = None
    relevance: float = Field(ge=0, le=1)
    opportunity_score: float = Field(ge=0, le=1)
    score_available: bool
    score_formula_version: str = "visibility_gap_v1"


class Insight(BaseModel):
    title: str
    detail: str
    query_uuid: str
    evidence_ids: list[str]
    opportunity_score: float = Field(ge=0, le=1)


class RecommendationDraft(BaseModel):
    target_query_uuid: str
    content_type: Literal["blog_post", "landing_page", "faq"]
    title: str
    rationale: str
    target_keywords: list[str]
    priority: Literal["high", "medium", "low"]
    evidence_ids: list[str]


class SynthesisPayload(BaseModel):
    insights: list[Insight] = Field(max_length=5)
    recommendations: list[RecommendationDraft] = Field(max_length=5)


class AnalysisResult(BaseModel):
    queries: list[QueryAnalysis]
    insights: list[Insight]
    recommendations: list[RecommendationDraft]
    synthesis_mode: Literal["llm", "deterministic_fallback", "mock"]
    limitations: list[str] = Field(default_factory=list)


class NodeEvent(BaseModel):
    event: str = "node_finished"
    node: str
    status: Literal["success", "failed", "skipped", "fallback"]
    duration_ms: float
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0


class Coverage(BaseModel):
    planned_calls: int
    attempted_calls: int
    successful_calls: int
    failed_calls: int
    rejected_tool_calls: int
    normalized_records: int
    api_attempts: int


class Report(BaseModel):
    schema_version: str = "1.0"
    run_uuid: str
    profile_uuid: str
    effective_question: str
    status: RunStatus
    data_mode: Literal["mock", "live"]
    llm_mode: Literal["mock", "live"]
    coverage: Coverage
    query_results: list[QueryAnalysis]
    insights: list[Insight]
    recommendations: list[RecommendationDraft]
    errors: list[PipelineError]
    limitations: list[str]
    summary: str
    started_at: datetime
    finished_at: datetime
    total_tokens_used: int | None = None
    token_usage_available: bool = False
    trace_id: str


def opportunity_score(
    relevance: float, visibility: VisibilityStatus, position: int | None
) -> tuple[float, bool]:
    if visibility is VisibilityStatus.UNKNOWN:
        return 0.0, False
    gap = (
        1.0
        if visibility is VisibilityStatus.NOT_VISIBLE
        else (0.5 if position and position > 3 else 0.2)
    )
    return round(max(0.0, min(1.0, relevance * gap)), 4), True


def recommendation_priority(score: float) -> Literal["high", "medium", "low"]:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"
