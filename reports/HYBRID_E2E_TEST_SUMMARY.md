# Hybrid End-to-End Test Summary

**Test date:** 2026-09-06  
**Selected configuration:** `DATAFORSEO_MODE=mock`, `LLM_MODE=live`  
**Mock scenario:** `happy_path`  
**OpenAI:** Real authenticated API calls  
**DataForSEO:** Deterministic provider-shaped fixtures through the production adapter interface

## Outcome

The complete hybrid workflow passed. OpenAI performed real tool-call planning and structured synthesis. DataForSEO-shaped fixtures exercised both retrieval branches, normalization, visibility analysis, scoring, persistence, report generation, listings, and query recheck.

| Check | Result |
|---|---|
| FastAPI startup | Passed |
| Profile creation | HTTP 201 |
| Live OpenAI planner | HTTP 200; 2 typed calls produced |
| Tool-call validation | 2 accepted; 0 rejected |
| Mock SERP retrieval | Passed |
| Mock AI visibility retrieval | Passed |
| Parallel graph join | Passed; normalization ran once |
| Evidence normalization | 4 records, 0 errors |
| Live OpenAI structured synthesis | HTTP 200; 2 insights and 2 recommendations |
| Report persistence | Passed; status `completed` |
| Query listing | HTTP 200; 2 queries |
| Recommendation listing | HTTP 200; 2 recommendations |
| Query recheck | Passed; stable query identity and completed report |
| Token accounting | Passed; available for full run and recheck |
| Secret handling | Passed; `.env` ignored and `.env.example` contains blank placeholders |

## Full run

- Profile UUID: `1e400072-1f96-47fb-bdf8-716c4ae99b61`
- Run UUID: `24f25664-969b-468d-94fa-5045400dc7f5`
- Trace ID: `94384455-0e15-45dd-bbe8-73f93398da22`
- Final status: `completed`
- Planned / attempted / successful calls: 2 / 2 / 2
- Failed and rejected calls: 0
- Normalized records: 4
- Insights: 2
- Recommendations: 2
- Total OpenAI tokens: 1,968
- Total wall time: approximately 8.28 seconds

Node timings:

| Node | Duration |
|---|---:|
| `query_planner` | 2,678.90 ms |
| `validate_plan` | 0.17 ms |
| `dispatch` | <0.01 ms |
| `serp_retrieval` | 0.27 ms |
| `ai_retrieval` | 0.29 ms |
| `normalize` | 0.23 ms |
| `analysis` | 5,596.27 ms |
| `report` | 0.24 ms |

The two retrieval branches ran in the same LangGraph superstep and converged at the normalization barrier.

## Recheck

- Run UUID: `6432e0f1-3c15-44ff-9489-153255d42c41`
- Trace ID: `f3f98082-231d-46c0-a01a-6e4a21c822ea`
- Final status: `completed`
- Planner: bypassed as designed
- Planned / attempted / successful calls: 1 / 1 / 1
- Normalized records: 2
- Live synthesis duration: 3,829.91 ms
- Total OpenAI tokens: 910
- Query UUID remained `5fea2adb-4f64-498d-943d-63bde3f8dee1`

## Configuration used for submission/demo

The ignored `.env` is configured as:

```dotenv
DATAFORSEO_MODE=mock
MOCK_SCENARIO=happy_path
LLM_MODE=live
```

Credential values are intentionally omitted. Run the verified workflow with:

```bash
make run PORT=8768
```

Then, in another terminal:

```bash
DEMO_BASE_URL=http://127.0.0.1:8768 DEMO_MAX_QUERIES=1 make demo
```

## Interpretation boundary

This validates real OpenAI tool selection and synthesis plus the entire application and persistence path. It does not validate live search facts. All visibility positions, citations, and evidence in this run come from the explicitly labeled `mock` DataForSEO fixtures. The separate live-test report records the verified DataForSEO credentials and the external `40104` account-verification blocker.

## Regression verification

- Pytest: 9 passed
- Ruff: passed
- Mypy: passed
- Live HTTP workflow: passed
- Server shutdown: clean

A nonfatal LangChain/Pydantic structured-output serialization warning appeared after successful OpenAI responses. Parsed structured output, validation, persistence, and responses were unaffected.
