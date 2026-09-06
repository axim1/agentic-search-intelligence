from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from search_intelligence.config import Settings
from search_intelligence.main import create_app


@pytest.fixture
def app_factory(tmp_path):
    apps = []

    def factory(scenario: str = "happy_path"):
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / (scenario + str(len(apps)) + '.db')}",
            dataforseo_mode="mock",
            llm_mode="mock",
            mock_scenario=scenario,
            backoff_base_seconds=0,
            backoff_cap_seconds=0,
        )
        app = create_app(settings)
        apps.append(app)
        return app

    return factory


@pytest.fixture
def profile_body():
    return {
        "name": "Surfer SEO",
        "domain": "surferseo.com",
        "industry": "SEO Software",
        "description": "AI-powered SEO content optimization tool",
        "competitors": ["clearscope.io", "marketmuse.com", "frase.io"],
    }


@pytest.fixture
def create_profile(profile_body):
    def create(client: TestClient):
        response = client.post("/api/v1/profiles", json=profile_body)
        assert response.status_code == 201
        return response.json()["profile_uuid"]

    return create
