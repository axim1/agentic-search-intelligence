from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from .domain import AIToolArgs, PipelineError, PlannedCall, ProposedToolCall, SerpToolArgs, ToolName


def _schema_only(**kwargs: Any) -> str:
    """The planner sees this schema; execution is delegated to the provider adapter."""
    return "validated"


SEARCH_GOOGLE_SERP = StructuredTool.from_function(
    func=_schema_only,
    name=ToolName.SERP.value,
    description="Retrieve Google organic SERP evidence for one keyword and locale.",
    args_schema=SerpToolArgs,
)
QUERY_CHATGPT_VISIBILITY = StructuredTool.from_function(
    func=_schema_only,
    name=ToolName.AI.value,
    description="Retrieve one structured ChatGPT response with web search for visibility evidence.",
    args_schema=AIToolArgs,
)
PLANNER_TOOLS = [SEARCH_GOOGLE_SERP, QUERY_CHATGPT_VISIBILITY]


def validate_proposed_calls(
    proposals: list[ProposedToolCall], max_calls: int
) -> tuple[list[PlannedCall], list[PipelineError]]:
    accepted: list[PlannedCall] = []
    errors: list[PipelineError] = []
    seen: set[tuple[str, str]] = set()
    schemas: dict[str, type[BaseModel]] = {
        ToolName.SERP.value: SerpToolArgs,
        ToolName.AI.value: AIToolArgs,
    }
    for proposal in proposals:
        if len(accepted) >= max_calls:
            errors.append(
                PipelineError(
                    code="call_budget_exceeded",
                    stage="validation",
                    message="Tool call exceeded the configured budget",
                )
            )
            continue
        schema = schemas.get(proposal.name)
        if schema is None:
            errors.append(
                PipelineError(
                    code="unknown_tool",
                    stage="validation",
                    message=f"Unknown tool: {proposal.name}",
                )
            )
            continue
        if proposal.parse_error or not isinstance(proposal.raw_arguments, dict):
            errors.append(
                PipelineError(
                    code="malformed_tool_arguments",
                    stage="validation",
                    message="Tool arguments were not a JSON object",
                )
            )
            continue
        try:
            args = schema.model_validate(proposal.raw_arguments)
        except ValidationError as exc:
            errors.append(
                PipelineError(code="invalid_tool_arguments", stage="validation", message=str(exc))
            )
            continue
        payload = args.model_dump()
        dedupe_key = (proposal.name, repr(sorted(payload.items())))
        if dedupe_key in seen:
            errors.append(
                PipelineError(
                    code="duplicate_tool_call",
                    stage="validation",
                    message="Duplicate tool call rejected",
                )
            )
            continue
        seen.add(dedupe_key)
        query_text = payload.get("keyword") or payload.get("user_prompt", "")
        accepted.append(
            PlannedCall(
                ordinal=len(accepted),
                intent_group=f"intent-{len(accepted) // 2 + 1}",
                query_text=str(query_text),
                tool_name=ToolName(proposal.name),
                arguments=payload,
                origin="llm",
            )
        )
    return accepted, errors
