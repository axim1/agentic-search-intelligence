from __future__ import annotations

import json
import os

import httpx

BASE = os.getenv("DEMO_BASE_URL", "http://127.0.0.1:8000")


def show(label: str, response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    print(f"\n{label}\n{json.dumps(payload, indent=2)}")
    return payload


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=90) as client:
        profile = show(
            "PROFILE",
            client.post(
                "/api/v1/profiles",
                json={
                    "name": "Surfer SEO",
                    "domain": "surferseo.com",
                    "industry": "SEO Software",
                    "description": "AI-powered SEO content optimization tool",
                    "competitors": ["clearscope.io", "marketmuse.com", "frase.io"],
                },
            ),
        )
        profile_id = profile["profile_uuid"]
        show(
            "RUN",
            client.post(
                f"/api/v1/profiles/{profile_id}/run",
                json={
                    "question": "How does Surfer SEO show up for best project management software?",
                    "max_queries": 2,
                },
            ),
        )
        queries = show("QUERIES", client.get(f"/api/v1/profiles/{profile_id}/queries"))
        show("RECOMMENDATIONS", client.get(f"/api/v1/profiles/{profile_id}/recommendations"))
        if queries["items"]:
            show(
                "RECHECK",
                client.post(f"/api/v1/queries/{queries['items'][0]['query_uuid']}/recheck"),
            )


if __name__ == "__main__":
    main()
