from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import Evidence, Profile, Query, Recommendation, RetrievalCall, Run
from .domain import PlannedCall, ProfileSnapshot, Report, ToolName, utcnow
from .graph import PipelineGraph

logger = logging.getLogger("search_intelligence")


class BusyError(Exception):
    pass


class NotFoundError(Exception):
    pass


class PipelineService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session], graph: PipelineGraph):
        self.settings = settings
        self.sessions = sessions
        self.graph = graph
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def create_profile(self, payload: dict[str, Any]) -> Profile:
        profile = Profile(**payload)
        with self.sessions() as session:
            session.add(profile)
            session.commit()
        return profile

    def get_profile(self, profile_uuid: str) -> Profile:
        with self.sessions() as session:
            profile = session.get(Profile, profile_uuid)
            if not profile:
                raise NotFoundError("Profile not found")
            session.expunge(profile)
            return profile

    def latest_full_run(self, session: Session, profile_uuid: str) -> Run | None:
        return session.scalar(
            select(Run)
            .where(Run.profile_uuid == profile_uuid, Run.kind == "full", Run.status != "running")
            .order_by(desc(Run.started_at), desc(Run.uuid))
            .limit(1)
        )

    async def run_profile(
        self, profile_uuid: str, question: str | None, max_queries: int
    ) -> Report:
        profile = self.get_profile(profile_uuid)
        effective = (
            question or f"How does {profile.name} appear for searches about {profile.industry}?"
        )
        return await self._execute(profile, "full", effective, max_queries=max_queries)

    async def recheck(self, query_uuid: str) -> Report:
        with self.sessions() as session:
            query = session.get(Query, query_uuid)
            if not query:
                raise NotFoundError("Query not found")
            profile = session.get(Profile, query.profile_uuid)
            assert profile is not None
            call = PlannedCall(
                query_uuid=query.uuid,
                ordinal=0,
                intent_group=query.intent_group,
                query_text=query.query_text,
                tool_name=ToolName(query.tool_name),
                arguments=query.tool_args,
                origin=query.origin,
            )
            session.expunge(profile)
        return await self._execute(
            profile,
            "recheck",
            query.query_text,
            plan=[call],
            target_query_uuid=query_uuid,
            max_queries=1,
        )

    async def _execute(
        self,
        profile: Profile,
        kind: str,
        question: str,
        max_queries: int,
        plan: list[PlannedCall] | None = None,
        target_query_uuid: str | None = None,
    ) -> Report:
        lock = self._locks[profile.uuid]
        if lock.locked():
            raise BusyError("A run for this profile is already in progress")
        async with lock:
            run_uuid, trace_id = str(uuid4()), str(uuid4())
            with self.sessions() as session:
                session.add(
                    Run(
                        uuid=run_uuid,
                        profile_uuid=profile.uuid,
                        kind=kind,
                        target_query_uuid=target_query_uuid,
                        status="running",
                        question=question,
                        trace_id=trace_id,
                    )
                )
                session.commit()
            snapshot = ProfileSnapshot(
                uuid=profile.uuid,
                name=profile.name,
                domain=profile.domain,
                industry=profile.industry,
                description=profile.description,
                competitors=profile.competitors,
            )
            initial: dict[str, Any] = {
                "run_uuid": run_uuid,
                "trace_id": trace_id,
                "run_kind": kind,
                "profile": snapshot,
                "question": question,
                "max_queries": max_queries,
                "started_at": utcnow(),
                "deadline_monotonic": time.monotonic() + self.settings.run_timeout_seconds,
                "node_events": [],
            }
            if plan is not None:
                initial["plan"] = plan
            try:
                state = await asyncio.wait_for(
                    self.graph.graph.ainvoke(initial), timeout=self.settings.run_timeout_seconds + 5
                )
                report: Report = state["report"]
                self._persist_final(state, report, kind, target_query_uuid)
                for event in state.get("node_events", []):
                    logger.info(
                        event.model_dump_json(), extra={"trace_id": trace_id, "run_uuid": run_uuid}
                    )
                logger.info(report.model_dump_json(), extra={"event": "run_summary"})
                return report
            except Exception as exc:
                with self.sessions() as session:
                    run = session.get(Run, run_uuid)
                    if run:
                        run.status = "failed"
                        run.finished_at = utcnow()
                        run.errors = [
                            {
                                "code": "internal_error",
                                "stage": "pipeline",
                                "message": type(exc).__name__,
                            }
                        ]
                        session.commit()
                raise

    def _persist_final(
        self, state: dict[str, Any], report: Report, kind: str, target_query_uuid: str | None
    ) -> None:
        with self.sessions() as session:
            run = session.get(Run, report.run_uuid)
            assert run is not None
            run.status, run.finished_at = report.status.value, report.finished_at
            run.planned_calls, run.normalized_records = (
                report.coverage.planned_calls,
                report.coverage.normalized_records,
            )
            run.report = report.model_dump(mode="json")
            run.errors = [item.model_dump(mode="json") for item in report.errors]
            run.metrics = {
                "coverage": report.coverage.model_dump(),
                "nodes": [item.model_dump(mode="json") for item in state.get("node_events", [])],
            }
            analyses = {item.query_uuid: item for item in report.query_results}
            if kind == "full":
                for call in state.get("plan", []):
                    qa = analyses.get(call.query_uuid)
                    session.add(
                        Query(
                            uuid=call.query_uuid,
                            profile_uuid=report.profile_uuid,
                            originating_run_uuid=report.run_uuid,
                            ordinal=call.ordinal,
                            intent_group=call.intent_group,
                            query_text=call.query_text,
                            tool_name=call.tool_name.value,
                            tool_args=call.arguments,
                            origin=call.origin,
                            visibility_status=qa.visibility_status.value if qa else "unknown",
                            domain_visible=qa.domain_visible if qa else False,
                            brand_mentioned=qa.brand_mentioned if qa else False,
                            visibility_position=qa.visibility_position if qa else None,
                            opportunity_score=qa.opportunity_score if qa else 0,
                            score_available=qa.score_available if qa else False,
                            last_checked_at=report.finished_at,
                            latest_check_run_uuid=report.run_uuid,
                        )
                    )
            elif target_query_uuid:
                query = session.get(Query, target_query_uuid)
                qa = analyses.get(target_query_uuid)
                if query and qa:
                    query.visibility_status, query.domain_visible = (
                        qa.visibility_status.value,
                        qa.domain_visible,
                    )
                    query.brand_mentioned, query.visibility_position = (
                        qa.brand_mentioned,
                        qa.visibility_position,
                    )
                    query.opportunity_score, query.score_available = (
                        qa.opportunity_score,
                        qa.score_available,
                    )
                    query.last_checked_at, query.latest_check_run_uuid = (
                        report.finished_at,
                        report.run_uuid,
                    )
                    session.execute(
                        update(Recommendation)
                        .where(Recommendation.target_query_uuid == target_query_uuid)
                        .values(active=False)
                    )
            session.flush()
            retrieval_ids: dict[str, str] = {}
            for result in state.get("serp_results", []) + state.get("ai_results", []):
                row = RetrievalCall(
                    run_uuid=report.run_uuid,
                    query_uuid=result.query_uuid,
                    tool_name=result.tool_name.value,
                    mode=result.mode,
                    status=result.outcome,
                    provider_task_id=result.provider_task_id,
                    attempts=result.attempt_count,
                    duration_ms=result.duration_ms,
                    error=result.error.model_dump(mode="json") if result.error else None,
                    raw_response=result.raw_payload,
                    retrieved_at=result.retrieved_at,
                )
                session.add(row)
                session.flush()
                retrieval_ids[result.query_uuid] = row.uuid
            for observation in state.get("observations", []):
                retrieval_uuid = retrieval_ids.get(observation.query_uuid)
                if not retrieval_uuid:
                    continue
                session.add(
                    Evidence(
                        uuid=observation.uuid,
                        retrieval_call_uuid=retrieval_uuid,
                        query_uuid=observation.query_uuid,
                        channel=observation.channel,
                        record_type=observation.record_type,
                        title=observation.title,
                        text=observation.text,
                        url=observation.url,
                        domain=observation.domain,
                        organic_rank=observation.organic_rank,
                        source_task_id=observation.source_task_id,
                        provenance=observation.provenance,
                        observed_at=observation.observed_at,
                    )
                )
            for draft in report.recommendations:
                session.add(
                    Recommendation(
                        profile_uuid=report.profile_uuid,
                        target_query_uuid=draft.target_query_uuid,
                        producing_run_uuid=report.run_uuid,
                        content_type=draft.content_type,
                        title=draft.title,
                        rationale=draft.rationale,
                        target_keywords=draft.target_keywords,
                        priority=draft.priority,
                        evidence_ids=draft.evidence_ids,
                        active=True,
                    )
                )
            session.commit()
