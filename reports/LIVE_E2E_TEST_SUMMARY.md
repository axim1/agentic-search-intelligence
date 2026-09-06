# Live End-to-End Test Summary

**Test date:** 2026-09-06  
**Configuration:** `LLM_MODE=live`, `DATAFORSEO_MODE=live`  
**Credentials:** Loaded from ignored `.env`; values were never printed or added to Git  
**Test profile:** Surfer SEO / `surferseo.com`  
**Research question:** “How does Surfer SEO show up for best project management software?”  
**Intent budget:** 1, producing at most 2 retrieval calls

## Result

The live HTTP workflow executed through profile creation, OpenAI planning, tool validation, parallel DataForSEO dispatch, persistence, query listing, recommendation listing, and single-query recheck. The final research result could not complete because DataForSEO requires account verification before either production or sandbox tasks can execute.

| Check | Outcome | Evidence |
|---|---|---|
| Application startup | Passed | FastAPI started on localhost and initialized SQLite |
| Profile creation | Passed | HTTP 201; profile `db51cc07-6e4e-4298-bbe4-164bd1e5a626` |
| OpenAI authentication/model call | Passed | Chat Completions returned HTTP 200 |
| Live query planning | Passed | Planner produced 2 calls; validation accepted 2 and rejected 0 |
| SERP tool selection | Passed | `search_google_serp` planned with validated arguments |
| AI visibility tool selection | Passed | `query_chatgpt_visibility` planned with validated arguments |
| DataForSEO credential authentication | Passed | Free `/v3/appendix/user_data` check returned HTTP 200 / provider `20000 Ok` |
| DataForSEO production SERP execution | Blocked externally | HTTP 403 / provider `40104 account verification required` |
| DataForSEO production AI execution | Blocked externally | HTTP 403 / provider `40104 account verification required` |
| DataForSEO sandbox execution | Blocked externally | HTTP 403 / provider `40104 account verification required` |
| Graceful failure | Passed | Both errors classified non-retryable; no pointless retries; failed report persisted |
| Query listing | Passed | HTTP 200; 2 queries returned with `visibility_status=unknown` |
| Recommendation listing | Passed | HTTP 200; empty because there was no usable evidence |
| Query recheck | Passed structurally | Saved SERP call reran without planning and persisted the same account error |
| Secret handling | Passed after correction | Credentials moved from tracked `.env.example` to ignored `.env`; template restored |

## Primary live run

- Run UUID: `cbcdb2c8-abfb-4932-85ec-4ce9c3558e39`
- Trace ID: `1de5d1bb-e892-4cae-9cdf-64eba3f4ada7`
- Persisted status: `failed`
- Planned / attempted calls: 2 / 2
- Successful / failed calls: 0 / 2
- API attempts: 2
- Normalized records: 0
- OpenAI planner duration: 2,806.83 ms
- SERP branch duration: 580.14 ms
- AI retrieval branch duration: 617.05 ms
- Both retrieval branches ran in parallel
- Total graph wall time: approximately 3.43 s
- Error code for both calls: `dataforseo_account_unverified`
- Provider status for both calls: `40104`
- Retryable: false

The system correctly avoided visibility and opportunity claims when no evidence was available. It returned no insights or recommendations and stored both queries as `unknown` with `score_available=false`.

## Recheck run

- Run UUID: `6ef247d1-e497-4dc2-847b-6874b1e091c8`
- Trace ID: `ffab11b8-0095-47c3-82bd-12dddf392761`
- Planned / attempted calls: 1 / 1
- Planner node: bypassed as designed
- SERP duration: 179.06 ms
- Persisted status: `failed`
- Provider status: `40104`

## Code corrections resulting from the live test

1. DataForSEO non-2xx JSON is now parsed so provider codes such as `40104`, `40200`, and `40203` are preserved instead of being hidden behind a generic HTTP error.
2. Retrieval errors are emitted once in reports; the live test exposed and removed duplicate aggregation.
3. The live planner instruction now explicitly requests one traditional-search and one AI-visibility call per selected intent, while the model still chooses the arguments.
4. The demo accepts `DEMO_MAX_QUERIES` so live smoke tests can minimize paid usage.

## Automated regression verification

- Pytest: 9 passed
- Ruff lint: passed
- Mypy static type check: passed
- Expected dependency warnings: one Starlette/AnyIO deprecation warning and one LangGraph pending-deprecation warning; neither affects execution

## Required action before a successful production-data run

Complete the DataForSEO account verification shown in the DataForSEO user panel. Provider code `40104` normally requires email and/or phone verification. After verification, rerun:

```bash
HTTP_READ_TIMEOUT_SECONDS=120 RUN_TIMEOUT_SECONDS=180 make run PORT=8768
DEMO_BASE_URL=http://127.0.0.1:8768 DEMO_MAX_QUERIES=1 make demo
```

The second command should be run in another terminal. A fully successful live result must show both DataForSEO calls with provider status `20000`, normalized evidence records greater than zero, and then reach live OpenAI synthesis. Live synthesis was not reached in this test because every retrieval call was rejected before evidence existed.
