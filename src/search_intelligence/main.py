from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router
from .config import Settings, get_settings
from .db import create_session_factory
from .graph import PipelineGraph
from .providers import DataForSEOProvider
from .service import PipelineService


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()
    logging.basicConfig(level=configured.log_level, format="%(message)s")
    sessions = create_session_factory(configured)
    provider = DataForSEOProvider(configured)
    graph = PipelineGraph(configured, provider)
    service = PipelineService(configured, sessions, graph)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service
        yield
        await provider.close()

    app = FastAPI(title="Agentic Search Intelligence", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.include_router(router)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("search_intelligence").exception("Unhandled request error")
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "An internal error occurred"},
                "trace_id": request.headers.get("x-correlation-id"),
            },
        )

    return app


app = create_app()
