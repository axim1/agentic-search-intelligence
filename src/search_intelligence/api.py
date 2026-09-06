from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi import Query as QueryParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from .db import Profile, Query, Recommendation, Run
from .domain import Report
from .service import BusyError, NotFoundError, PipelineService

DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253)
    industry: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    competitors: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("domain", mode="before")
    @classmethod
    def normalize_domain(cls, value: Any) -> str:
        domain = (
            str(value)
            .strip()
            .lower()
            .removeprefix("https://")
            .removeprefix("http://")
            .split("/")[0]
            .removeprefix("www.")
            .rstrip(".")
        )
        if not DOMAIN_RE.fullmatch(domain):
            raise ValueError("domain must be a hostname such as example.com")
        return domain

    @field_validator("competitors", mode="after")
    @classmethod
    def normalize_competitors(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            normalized.append(cls.normalize_domain(value))
        return list(dict.fromkeys(normalized))


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str | None = Field(default=None, min_length=3, max_length=2000)
    max_queries: int = Field(default=2, ge=1, le=3)


def get_service(request: Request) -> PipelineService:
    return request.app.state.service


def _uuid(value: UUID) -> str:
    return str(value)


router = APIRouter()


@router.post("/api/v1/profiles", status_code=201)
def create_profile(
    payload: ProfileCreate, service: PipelineService = Depends(get_service)
) -> dict[str, Any]:
    profile = service.create_profile(payload.model_dump())
    return {
        "profile_uuid": profile.uuid,
        "name": profile.name,
        "domain": profile.domain,
        "status": "created",
        "created_at": profile.created_at,
    }


@router.get("/api/v1/profiles/{profile_uuid}")
def get_profile(
    profile_uuid: UUID, service: PipelineService = Depends(get_service)
) -> dict[str, Any]:
    try:
        profile = service.get_profile(_uuid(profile_uuid))
    except NotFoundError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": str(exc)}) from exc
    with service.sessions() as session:
        total = (
            session.scalar(
                select(func.count()).select_from(Run).where(Run.profile_uuid == profile.uuid)
            )
            or 0
        )
        latest_full = service.latest_full_run(session, profile.uuid)
        latest = service.latest_run(session, profile.uuid)
        scores: list[float] = []
        if latest_full:
            scores = list(
                session.scalars(
                    select(Query.opportunity_score).where(
                        Query.originating_run_uuid == latest_full.uuid,
                        Query.score_available.is_(True),
                    )
                )
            )
    return {
        "profile_uuid": profile.uuid,
        "name": profile.name,
        "domain": profile.domain,
        "industry": profile.industry,
        "description": profile.description,
        "competitors": profile.competitors,
        "created_at": profile.created_at,
        "total_dag_runs": total,
        "latest_run_status": latest.status if latest else None,
        "latest_run_kind": latest.kind if latest else None,
        "average_opportunity_score": round(sum(scores) / len(scores), 4) if scores else None,
    }


@router.post("/api/v1/profiles/{profile_uuid}/run", response_model=Report)
async def run_profile(
    profile_uuid: UUID,
    payload: RunRequest = Body(default_factory=RunRequest),
    service: PipelineService = Depends(get_service),
) -> Report:
    try:
        return await service.run_profile(_uuid(profile_uuid), payload.question, payload.max_queries)
    except NotFoundError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": str(exc)}) from exc
    except BusyError as exc:
        raise HTTPException(409, detail={"code": "profile_busy", "message": str(exc)}) from exc


def _query_json(item: Query) -> dict[str, Any]:
    return {
        "query_uuid": item.uuid,
        "run_uuid": item.originating_run_uuid,
        "query_text": item.query_text,
        "tool_name": item.tool_name,
        "estimated_search_volume": item.estimated_search_volume or 0,
        "competitive_difficulty": item.competitive_difficulty or 0,
        "metric_availability": {
            "estimated_search_volume": "unavailable"
            if item.estimated_search_volume is None
            else "measured",
            "competitive_difficulty": "unavailable"
            if item.competitive_difficulty is None
            else "measured",
        },
        "opportunity_score": item.opportunity_score,
        "score_available": item.score_available,
        "domain_visible": item.domain_visible,
        "visibility_status": item.visibility_status,
        "brand_mentioned": item.brand_mentioned,
        "visibility_position": item.visibility_position,
        "discovered_at": item.discovered_at,
        "last_checked_at": item.last_checked_at,
        "latest_check_run_uuid": item.latest_check_run_uuid,
    }


@router.get("/api/v1/profiles/{profile_uuid}/queries")
def list_queries(
    profile_uuid: UUID,
    min_score: float | None = QueryParam(default=None, ge=0, le=1),
    status: Literal["visible", "not_visible", "unknown"] | None = None,
    page: int = QueryParam(default=1, ge=1),
    per_page: int = QueryParam(default=20, ge=1, le=100),
    service: PipelineService = Depends(get_service),
) -> dict[str, Any]:
    with service.sessions() as session:
        if not session.get(Profile, _uuid(profile_uuid)):
            raise HTTPException(404, detail={"code": "not_found", "message": "Profile not found"})
        latest = service.latest_full_run(session, _uuid(profile_uuid))
        if not latest:
            return {"run_uuid": None, "items": [], "page": page, "per_page": per_page, "total": 0}
        statement = select(Query).where(Query.originating_run_uuid == latest.uuid)
        if min_score is not None:
            statement = statement.where(
                Query.score_available.is_(True), Query.opportunity_score >= min_score
            )
        if status:
            statement = statement.where(Query.visibility_status == status)
        count = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(
            session.scalars(
                statement.order_by(Query.opportunity_score.desc(), Query.ordinal, Query.uuid)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )
        return {
            "run_uuid": latest.uuid,
            "items": [_query_json(item) for item in items],
            "page": page,
            "per_page": per_page,
            "total": count,
        }


@router.get("/api/v1/profiles/{profile_uuid}/recommendations")
def list_recommendations(
    profile_uuid: UUID, service: PipelineService = Depends(get_service)
) -> dict[str, Any]:
    with service.sessions() as session:
        if not session.get(Profile, _uuid(profile_uuid)):
            raise HTTPException(404, detail={"code": "not_found", "message": "Profile not found"})
        latest = service.latest_full_run(session, _uuid(profile_uuid))
        if not latest:
            return {"run_uuid": None, "items": [], "total": 0}
        query_ids = select(Query.uuid).where(Query.originating_run_uuid == latest.uuid)
        rows = list(
            session.scalars(
                select(Recommendation)
                .where(
                    Recommendation.target_query_uuid.in_(query_ids), Recommendation.active.is_(True)
                )
                .order_by(Recommendation.created_at.desc(), Recommendation.uuid)
            )
        )
        items = [
            {
                "recommendation_uuid": row.uuid,
                "target_query_uuid": row.target_query_uuid,
                "content_type": row.content_type,
                "title": row.title,
                "rationale": row.rationale,
                "target_keywords": row.target_keywords,
                "priority": row.priority,
            }
            for row in rows
        ]
        return {"run_uuid": latest.uuid, "items": items, "total": len(items)}


@router.post("/api/v1/queries/{query_uuid}/recheck", response_model=Report)
async def recheck_query(
    query_uuid: UUID, service: PipelineService = Depends(get_service)
) -> Report:
    try:
        return await service.recheck(_uuid(query_uuid))
    except NotFoundError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": str(exc)}) from exc
    except BusyError as exc:
        raise HTTPException(409, detail={"code": "profile_busy", "message": str(exc)}) from exc


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
