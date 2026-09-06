from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import Settings
from .domain import utcnow


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(253), index=True)
    industry: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    competitors: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    runs: Mapped[list[Run]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    profile_uuid: Mapped[str] = mapped_column(ForeignKey("profiles.uuid"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    target_query_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    question: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_calls: Mapped[int] = mapped_column(Integer, default=0)
    normalized_records: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    profile: Mapped[Profile] = relationship(back_populates="runs")
    queries: Mapped[list[Query]] = relationship(back_populates="originating_run")


class Query(Base):
    __tablename__ = "queries"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_uuid: Mapped[str] = mapped_column(ForeignKey("profiles.uuid"), index=True)
    originating_run_uuid: Mapped[str] = mapped_column(ForeignKey("runs.uuid"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    intent_group: Mapped[str] = mapped_column(String(100))
    query_text: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(20))
    visibility_status: Mapped[str] = mapped_column(String(20), default="unknown")
    domain_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competitive_difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    score_available: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_check_run_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    originating_run: Mapped[Run] = relationship(back_populates="queries")


class Recommendation(Base):
    __tablename__ = "recommendations"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    profile_uuid: Mapped[str] = mapped_column(ForeignKey("profiles.uuid"), index=True)
    target_query_uuid: Mapped[str] = mapped_column(ForeignKey("queries.uuid"), index=True)
    producing_run_uuid: Mapped[str] = mapped_column(ForeignKey("runs.uuid"), index=True)
    content_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(500))
    rationale: Mapped[str] = mapped_column(Text)
    target_keywords: Mapped[list[str]] = mapped_column(JSON)
    priority: Mapped[str] = mapped_column(String(20))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetrievalCall(Base):
    __tablename__ = "retrieval_calls"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_uuid: Mapped[str] = mapped_column(ForeignKey("runs.uuid"), index=True)
    query_uuid: Mapped[str] = mapped_column(ForeignKey("queries.uuid"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    provider_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Evidence(Base):
    __tablename__ = "observations"
    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    retrieval_call_uuid: Mapped[str] = mapped_column(ForeignKey("retrieval_calls.uuid"), index=True)
    query_uuid: Mapped[str] = mapped_column(ForeignKey("queries.uuid"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    record_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    organic_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_runs_profile_kind_started", Run.profile_uuid, Run.kind, Run.started_at)
Index("ix_queries_run_score", Query.originating_run_uuid, Query.opportunity_score)
Index("ix_retrieval_run_query", RetrievalCall.run_uuid, RetrievalCall.query_uuid)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    settings.ensure_data_directory()
    kwargs: dict[str, Any] = {"future": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(settings.database_url, **kwargs)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for run in session.query(Run).filter(Run.status == "running").all():
            run.status = "failed"
            run.finished_at = utcnow()
            run.errors = [
                {"code": "interrupted", "stage": "startup", "message": "Run was interrupted"}
            ]
        session.commit()
    return sessionmaker(engine, expire_on_commit=False, class_=Session)
