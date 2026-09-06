from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/search_intelligence.db"
    log_level: str = "INFO"
    capture_audit_payloads: bool = False
    audit_payload_max_chars: int = Field(50000, ge=1000, le=500000)
    dataforseo_mode: Literal["mock", "live"] = "mock"
    mock_scenario: Literal[
        "happy_path", "transient_then_success", "partial_failure", "all_failed"
    ] = "happy_path"
    dataforseo_login: str | None = None
    dataforseo_password: str | None = None
    dataforseo_chatgpt_model: str = "gpt-4.1-mini"
    llm_mode: Literal["mock", "live"] = "mock"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: str | None = None
    default_location_code: int = 2840
    default_language_code: str = "en"
    default_country_iso_code: str = "US"
    default_max_queries: int = Field(2, ge=1, le=3)
    max_retrieval_calls: int = Field(6, ge=1, le=12)
    dataforseo_max_concurrency: int = Field(2, ge=1, le=10)
    http_connect_timeout_seconds: float = Field(3, gt=0)
    http_read_timeout_seconds: float = Field(20, gt=0)
    http_write_timeout_seconds: float = Field(5, gt=0)
    http_pool_timeout_seconds: float = Field(3, gt=0)
    max_api_attempts: int = Field(3, ge=1, le=5)
    backoff_base_seconds: float = Field(0.5, ge=0)
    backoff_cap_seconds: float = Field(8, ge=0)
    run_timeout_seconds: float = Field(60, gt=5)

    @model_validator(mode="after")
    def validate_live_credentials(self) -> Settings:
        if self.dataforseo_mode == "live" and not (
            self.dataforseo_login and self.dataforseo_password
        ):
            raise ValueError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required in live mode")
        if self.llm_mode == "live" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required in live LLM mode")
        return self

    def ensure_data_directory(self) -> None:
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            path = Path(self.database_url.removeprefix(prefix))
            if path.parent != Path("."):
                path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
