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
| Insight score contract | Passed; relevance and opportunity score returned per insight |
| Structured node logs | Passed; redacted inputs, outputs, duration, status, retries, run and trace IDs |
| Run metrics | Passed; per-node rates/latency and per-tool API counts persisted |
| Secret handling | Passed; `.env` ignored and `.env.example` contains blank placeholders |

## Full run

- Profile UUID: `01ed7de7-7d1c-4fc6-9464-156b7ae172f5`
- Run UUID: `737e33e5-77b1-428e-931f-51156eafdc44`
- Trace ID: `83a34b32-0964-4ebc-ac73-dbce3865c794`
- Final status: `completed`
- Planned / attempted / successful calls: 2 / 2 / 2
- Failed and rejected calls: 0
- Normalized records: 4
- Insights: 2
- Recommendations: 2
- Total OpenAI tokens: 1,917
- Total wall time: approximately 9.30 seconds

Node timings:

| Node | Duration |
|---|---:|
| `query_planner` | 3,224.09 ms |
| `validate_plan` | 0.168 ms |
| `dispatch` | <0.01 ms |
| `serp_retrieval` | 0.255 ms |
| `ai_retrieval` | 0.268 ms |
| `normalize` | 0.224 ms |
| `analysis` | 6,068.56 ms |
| `report` | 0.284 ms |

The two retrieval branches ran in the same LangGraph superstep and converged at the normalization barrier.

## Recheck

- Run UUID: `f1e90590-63f1-4d49-ad56-fd3679c89332`
- Trace ID: `eb62f4cc-270d-494d-83b8-86504e316dfe`
- Final status: `completed`
- Planner: bypassed as designed
- Planned / attempted / successful calls: 1 / 1 / 1
- Normalized records: 2
- Live synthesis duration: 4,213.61 ms
- Total OpenAI tokens: 922
- Query UUID remained `f65dbb8f-0057-41cb-a549-a958ffc0aabf`

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

## Detailed audit verification

Detailed payload capture was enabled for a subsequent live OpenAI hybrid run:

- Run UUID: `fc539764-52e5-4c72-a2b8-0ff257101d4f`
- Trace ID: `11ef698f-5046-4d7a-b738-6feaaa266e67`
- Audit records persisted: 4
- OpenAI planning request/response: captured with tool calls and token usage
- DataForSEO SERP request/response: captured
- DataForSEO AI visibility request/response: captured
- OpenAI synthesis request/response: captured with token usage
- Authorization headers and credential fields: absent
- `GET /api/v1/runs/{run_uuid}/logs`: covered by the automated suite

## Regression verification

- Pytest: 11 passed
- Ruff: passed
- Mypy: passed
- Live HTTP workflow: passed
- Server shutdown: clean

A nonfatal LangChain/Pydantic structured-output serialization warning appeared after successful OpenAI responses. Parsed structured output, validation, persistence, and responses were unaffected.
