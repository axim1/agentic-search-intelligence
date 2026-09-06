from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from search_intelligence.domain import ProposedToolCall
from search_intelligence.providers import DataForSEOProvider
from search_intelligence.tools import validate_proposed_calls


def test_happy_path_all_required_endpoints(app_factory, create_profile):
    with TestClient(app_factory()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        profile_id = create_profile(client)
        run = client.post(
            f"/api/v1/profiles/{profile_id}/run",
            json={"question": "How visible is Surfer SEO?", "max_queries": 2},
        )
        assert run.status_code == 200
        body = run.json()
        assert body["status"] == "completed"
        assert body["coverage"]["planned_calls"] == 4
        assert body["coverage"]["normalized_records"] > 0
        assert body["insights"]
        assert all(0 <= insight["relevance"] <= 1 for insight in body["insights"])
        assert all(0 <= insight["opportunity_score"] <= 1 for insight in body["insights"])
        queries = client.get(f"/api/v1/profiles/{profile_id}/queries").json()
        assert queries["total"] == 4
        assert all("metric_availability" in item for item in queries["items"])
        recommendations = client.get(f"/api/v1/profiles/{profile_id}/recommendations")
        assert recommendations.status_code == 200
        query_id = queries["items"][0]["query_uuid"]
        recheck = client.post(f"/api/v1/queries/{query_id}/recheck")
        assert recheck.status_code == 200
        assert recheck.json()["coverage"]["planned_calls"] == 1
        detail = client.get(f"/api/v1/profiles/{profile_id}").json()
        assert detail["total_dag_runs"] == 2


def test_transient_failure_recovers_with_bounded_retries(app_factory, create_profile):
    app = app_factory("transient_then_success")
    with TestClient(app) as client:
        profile_id = create_profile(client)
        body = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 1}).json()
        assert body["status"] == "completed"
        assert body["coverage"]["planned_calls"] == 2
        assert body["coverage"]["api_attempts"] == 4
        with app.state.service.sessions() as session:
            from search_intelligence.db import Run

            row = session.get(Run, body["run_uuid"])
            assert row is not None
            nodes = row.metrics["nodes"]
            retrievals = [event for event in nodes if event["node"].endswith("_retrieval")]
            assert all(event["input_summary"]["calls"] == 1 for event in retrievals)
            assert all(event["retry_count"] == 1 for event in retrievals)
            assert all(event["trace_id"] == body["trace_id"] for event in nodes)
            assert all(event["run_uuid"] == body["run_uuid"] for event in nodes)
            summary = row.metrics["summary"]
            assert summary["per_node"]["serp_retrieval"]["success_rate"] == 1.0
            assert summary["per_node"]["serp_retrieval"]["retry_count"] == 1
            assert summary["api_calls"]["search_google_serp"]["attempts"] == 2


def test_partial_failure_preserves_successful_branch(app_factory, create_profile):
    with TestClient(app_factory("partial_failure")) as client:
        profile_id = create_profile(client)
        body = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 1}).json()
        assert body["status"] == "partial"
        assert body["coverage"]["successful_calls"] == 1
        assert body["coverage"]["failed_calls"] == 1
        assert body["coverage"]["normalized_records"] > 0


def test_all_failed_returns_persisted_failed_report(app_factory, create_profile):
    with TestClient(app_factory("all_failed")) as client:
        profile_id = create_profile(client)
        response = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert body["insights"] == []
        assert body["recommendations"] == []
        assert client.get(f"/api/v1/profiles/{profile_id}").json()["latest_run_status"] == "failed"


def test_malformed_tool_arguments_never_enter_plan():
    calls = [
        ProposedToolCall(
            name="search_google_serp", raw_arguments={"location_code": 2840, "language_code": "en"}
        ),
        ProposedToolCall(name="arbitrary_http", raw_arguments={"url": "https://example.com"}),
    ]
    plan, errors = validate_proposed_calls(calls, 6)
    assert plan == []
    assert {error.code for error in errors} == {"invalid_tool_arguments", "unknown_tool"}


def test_query_filters_and_validation(app_factory, create_profile):
    with TestClient(app_factory()) as client:
        profile_id = create_profile(client)
        client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 2})
        assert (
            client.get(f"/api/v1/profiles/{profile_id}/queries?status=visible").status_code == 200
        )
        assert client.get(f"/api/v1/profiles/{profile_id}/queries?min_score=2").status_code == 422
        assert client.get(f"/api/v1/profiles/{profile_id}/queries?page=0").status_code == 422


def test_invalid_profile_and_unknown_resources(app_factory, profile_body):
    with TestClient(app_factory()) as client:
        bad = dict(profile_body, domain="https://not a host/")
        assert client.post("/api/v1/profiles", json=bad).status_code == 422
        assert (
            client.get("/api/v1/profiles/00000000-0000-0000-0000-000000000000").status_code == 404
        )
        assert (
            client.post("/api/v1/queries/00000000-0000-0000-0000-000000000000/recheck").status_code
            == 404
        )


def test_graph_join_and_report_execute_once(app_factory, create_profile):
    app = app_factory()
    with TestClient(app) as client:
        profile_id = create_profile(client)
        body = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 2}).json()
        with app.state.service.sessions() as session:
            from search_intelligence.db import Run

            row = session.get(Run, body["run_uuid"])
            names = [event["node"] for event in row.metrics["nodes"]]
        assert names.count("normalize") == 1
        assert names.count("report") == 1
        assert "serp_retrieval" in names and "ai_retrieval" in names


def test_profile_status_uses_most_recent_recheck(app_factory, create_profile):
    app = app_factory()
    with TestClient(app) as client:
        profile_id = create_profile(client)
        full = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 1}).json()
        queries = client.get(f"/api/v1/profiles/{profile_id}/queries").json()
        app.state.service.settings.mock_scenario = "all_failed"
        recheck = client.post(f"/api/v1/queries/{queries['items'][0]['query_uuid']}/recheck").json()
        assert full["status"] == "completed"
        assert recheck["status"] == "failed"
        profile = client.get(f"/api/v1/profiles/{profile_id}").json()
        assert profile["latest_run_status"] == "failed"
        assert profile["latest_run_kind"] == "recheck"


def test_detailed_provider_audit_payloads_are_opt_in_and_redacted(app_factory, create_profile):
    app = app_factory()
    app.state.service.settings.capture_audit_payloads = True
    with TestClient(app) as client:
        profile_id = create_profile(client)
        body = client.post(f"/api/v1/profiles/{profile_id}/run", json={"max_queries": 1}).json()
        with app.state.service.sessions() as session:
            from search_intelligence.db import Run

            row = session.get(Run, body["run_uuid"])
            assert row is not None
            audits = row.metrics["audit_events"]
        logs = client.get(f"/api/v1/runs/{body['run_uuid']}/logs")
        assert logs.status_code == 200
        assert logs.json()["trace_id"] == body["trace_id"]
        assert len(logs.json()["audit_events"]) == 2
        assert len(audits) == 2
        assert {event["provider"] for event in audits} == {"dataforseo"}
        assert all(event["request_payload"]["arguments"] for event in audits)
        assert all(event["response_payload"]["tasks"] for event in audits)
        serialized = json.dumps(audits).lower()
        assert "authorization" not in serialized
        assert "dataforseo_password" not in serialized
        assert (
            client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/logs").status_code
            == 404
        )


def test_dataforseo_account_verification_error_is_classified():
    response = httpx.Response(
        403,
        json={
            "status_code": 40104,
            "status_message": "Please verify your account before using the API.",
        },
    )
    code, message, status = DataForSEOProvider._http_error_details(response)
    assert code == "dataforseo_account_unverified"
    assert status == 40104
    assert "verify" in message.lower()
