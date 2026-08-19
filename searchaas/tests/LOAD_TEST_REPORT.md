    # AiSearch Load Test Report

    **Generated:** 2026-07-24 18:59 UTC  
    **Host:** https://AiSearch-api-787220387490.us-central1.run.app/  
    **Collection:** `employee_support` · vector index: `employee_support_vector_index` · search index: `employee_support_search_index`  
    **Concurrency:** 200 virtual users · think time 1.0–4.0 s  
    **Duration:** 100 s  

    ---

    ## 1. Overall Verdict

    🔴 **FAIL** — Failure rate exceeds threshold. Server needs tuning before production use.

    ---

    ## 2. Load Generator Machine Config

    | Property | Value |
|---|---|
| OS | Darwin Darwin (arm64) |
| CPU (logical) | 10 vCPU |
| CPU (physical) | 10 cores |
| CPU freq (max) | 3.23 GHz |
| RAM total | 16.0 GB |
| RAM available | 4.3 GB |
| Python | 3.13.2 |
| Locust workers | 1 (master) |


    ---

    ## 3. Executive Summary

    | Metric | Value |
    |---|---|
    | Total requests | 3,647 |
    | Failures | **105 (2.9%)** |
    | TPS (transactions/sec) | **36.6** |
    | Successful TPS | **35.5** |
    | p50 latency | **440 ms** |
    | p95 latency | **5500 ms** |
    | p99 latency | **7800 ms** |
    | Max latency | **9733 ms** |
    | Run duration | **100 s** |

    ---

    ## 4. Capacity Analysis & Maximum Sustainable Load

    ⚠️ Minor failures (2.9%). Effective throughput of successful requests: **35.5 TPS**. Reduce concurrency by ~12% for a stable baseline.

### TPS by endpoint

| Endpoint | TPS | p50 (ms) | Fail % |
|---|---|---|---|
| `POST /retrieve/hybrid [HR]` | 6.30 | 460 | ⚠️ 3% |
| `POST /retrieve/hybrid [IT]` | 5.85 | 460 | ⚠️ 4% |
| `POST /retrieve/vector [HR]` | 4.27 | 450 | ⚠️ 3% |
| `POST /retrieve/vector [IT]` | 4.23 | 450 | ⚠️ 1% |
| `POST /retrieve/fulltext [IT]` | 3.07 | 340 | ✅ 0% |
| `POST /retrieve/fulltext [HR]` | 2.90 | 330 | ✅ 0% |
| `POST /retrieve/hybrid [top_k var]` | 2.43 | 470 | ⚠️ 3% |
| `POST /retrieve/vector [vague]` | 2.26 | 450 | ⚠️ 4% |
| `POST /retrieve [auto, HR]` | 1.49 | 410 | ⚠️ 4% |
| `POST /retrieve [auto, IT]` | 1.33 | 460 | ⚠️ 5% |
| `GET /health` | 1.22 | 290 | ✅ 0% |
| `GET /settings` | 0.77 | 290 | ✅ 0% |
| `POST /retrieve [auto, summarize=false]` | 0.44 | 450 | ❌ 9% |

### Capacity model (Little's Law)

```
  TPS  = N / (think_time + response_time)
       = 200 / (2.5s + 0.44s)
       = 68.0 TPS  ← theoretical max at this concurrency

  Observed TPS : 36.6  (2.9% failure rate)
  Run duration : 100 s
  Total reqs   : 3,647  (success: 3,542  fail: 105)
```

    ---

    ## 5. Per-Endpoint Statistics

    | Endpoint | Reqs | Failures | Avg (ms) | p50 | p90 | p95 | p99 | Max | TPS |
    |---|---|---|---|---|---|---|---|---|---|
    | `GET /health` | 122 | 0 | 351 | 290 | 310 | 420 | 3400 | 3506 | 1.22 |
| `POST /retrieve [auto, HR]` | 149 | **6** (4.0%) | 949 | 410 | 1500 | 7700 | 7900 | 8844 | 1.49 |
| `POST /retrieve [auto, IT]` | 133 | **7** (5.3%) | 1122 | 460 | 1600 | 7700 | 7900 | 8932 | 1.33 |
| `POST /retrieve [auto, summarize=false]` | 44 | **4** (9.1%) | 1404 | 450 | 5500 | 7600 | 7700 | 7666 | 0.44 |
| `POST /retrieve/fulltext [HR]` | 289 | 0 | 413 | 330 | 420 | 460 | 4000 | 4145 | 2.90 |
| `POST /retrieve/fulltext [IT]` | 306 | 0 | 422 | 340 | 410 | 490 | 3400 | 4360 | 3.07 |
| `POST /retrieve/hybrid [HR]` | 628 | **20** (3.2%) | 1153 | 460 | 3600 | 7600 | 7900 | 9698 | 6.30 |
| `POST /retrieve/hybrid [IT]` | 584 | **28** (4.8%) | 1196 | 460 | 3600 | 7700 | 7900 | 9664 | 5.85 |
| `POST /retrieve/hybrid [top_k var]` | 242 | **8** (3.3%) | 1080 | 470 | 1600 | 5500 | 8700 | 9733 | 2.43 |
| `POST /retrieve/vector [HR]` | 426 | **16** (3.8%) | 1128 | 450 | 1700 | 7600 | 7700 | 9425 | 4.27 |
| `POST /retrieve/vector [IT]` | 422 | **6** (1.4%) | 966 | 450 | 1600 | 3700 | 7700 | 8346 | 4.23 |
| `POST /retrieve/vector [vague]` | 225 | **10** (4.4%) | 1124 | 450 | 3600 | 7600 | 7800 | 9321 | 2.26 |
| `GET /settings` | 77 | 0 | 327 | 290 | 410 | 470 | 1900 | 1913 | 0.77 |

    ---

    ## 6. Tail Latency Spread (p99 / p50 ratio)

    A healthy endpoint has a spread < 5×. Anything ≥ 10× indicates sporadic severe outliers.

    | Endpoint | p50 (ms) | p99 (ms) | Max (ms) | Spread | Status |
    |---|---|---|---|---|---|
    | `/retrieve [auto, HR]` | 410 | 7900 | 8844 | 19.3× | 🔴 |
| `/retrieve [auto, IT]` | 460 | 7900 | 8932 | 17.2× | 🔴 |
| `/retrieve [auto, summarize=false]` | 450 | 7700 | 7666 | 17.1× | 🔴 |
| `/retrieve/fulltext [HR]` | 330 | 4000 | 4145 | 12.1× | 🔴 |
| `/retrieve/fulltext [IT]` | 340 | 3400 | 4360 | 10.0× | 🔴 |
| `/retrieve/hybrid [HR]` | 460 | 7900 | 9698 | 17.2× | 🔴 |
| `/retrieve/hybrid [IT]` | 460 | 7900 | 9664 | 17.2× | 🔴 |
| `/retrieve/hybrid [top_k var]` | 470 | 8700 | 9733 | 18.5× | 🔴 |
| `/retrieve/vector [HR]` | 450 | 7700 | 9425 | 17.1× | 🔴 |
| `/retrieve/vector [IT]` | 450 | 7700 | 8346 | 17.1× | 🔴 |
| `/retrieve/vector [vague]` | 450 | 7800 | 9321 | 17.3× | 🔴 |

    🟢 < 5× &nbsp; 🟡 5–10× &nbsp; 🔴 ≥ 10×

    ---

    ## 7. SLA Assessment

    | Endpoint | p50 | p95 | p99 |
    |---|---|---|---|
    | `GET /health` | ❌ 290 ms (≤50) | ❌ 420 ms (≤200) | ❌ 3400 ms (≤500) |
| `POST /retrieve [auto, HR]` | ✅ 410 ms (≤2000) | ❌ 7700 ms (≤3000) | ❌ 7900 ms (≤5000) |
| `POST /retrieve [auto, IT]` | ✅ 460 ms (≤2000) | ❌ 7700 ms (≤3000) | ❌ 7900 ms (≤5000) |
| `POST /retrieve [auto, summarize=false]` | ✅ 450 ms (≤5000) | ✅ 7600 ms (≤8000) | ✅ 7700 ms (≤10000) |
| `POST /retrieve/fulltext [HR]` | ✅ 330 ms (≤500) | ✅ 460 ms (≤800) | ❌ 4000 ms (≤1000) |
| `POST /retrieve/fulltext [IT]` | ✅ 340 ms (≤500) | ✅ 490 ms (≤800) | ❌ 3400 ms (≤1000) |
| `POST /retrieve/hybrid [HR]` | ✅ 460 ms (≤800) | ❌ 7600 ms (≤1500) | ❌ 7900 ms (≤3000) |
| `POST /retrieve/hybrid [IT]` | ✅ 460 ms (≤800) | ❌ 7700 ms (≤1500) | ❌ 7900 ms (≤3000) |
| `POST /retrieve/hybrid [top_k var]` | ✅ 470 ms (≤800) | ❌ 5500 ms (≤1500) | ❌ 8700 ms (≤3000) |
| `POST /retrieve/vector [HR]` | ✅ 450 ms (≤800) | ❌ 7600 ms (≤1500) | ❌ 7700 ms (≤3000) |
| `POST /retrieve/vector [IT]` | ✅ 450 ms (≤800) | ❌ 3700 ms (≤1500) | ❌ 7700 ms (≤3000) |
| `POST /retrieve/vector [vague]` | ✅ 450 ms (≤800) | ❌ 7600 ms (≤1500) | ❌ 7800 ms (≤3000) |
| `GET /settings` | ❌ 290 ms (≤50) | ❌ 470 ms (≤200) | ❌ 1900 ms (≤500) |

    **SLA thresholds used:**

    | Group | p50 | p95 | p99 |
    |---|---|---|---|
    | Infrastructure (`/health`, `/settings`) | 50 ms | 200 ms | 500 ms |
    | Fulltext (`/retrieve/fulltext`) | 500 ms | 800 ms | 1,000 ms |
    | Vector (`/retrieve/vector`) | 800 ms | 1,500 ms | 3,000 ms |
    | Hybrid (`/retrieve/hybrid`) | 800 ms | 1,500 ms | 3,000 ms |
    | Auto-route (`/retrieve`) | 2,000 ms | 3,000 ms | 5,000 ms |
    | Summarize (`/retrieve` + summarize) | 5,000 ms | 8,000 ms | 10,000 ms |

    ---

    ## 8. Findings & Recommendations

    - ❌ High failure rate (2.88%) — server is struggling; reduce concurrency or fix errors.
- ❌ **High tail latency** on `/retrieve [auto, HR]`: p99 (7900 ms) is 19× the p50 (410 ms). Likely cause: LLM call (QueryUnderstanding + RetrievalPlanner) has no timeout — slow Gemini responses hang the thread. Fix: set `planner.llm_timeout_s` in AiSearch.yaml (default 5 s) and redeploy.
- ⚠️ **Max latency spike** on `/retrieve [auto, HR]`: 8844 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve [auto, IT]`: p99 (7900 ms) is 17× the p50 (460 ms). Likely cause: LLM call (QueryUnderstanding + RetrievalPlanner) has no timeout — slow Gemini responses hang the thread. Fix: set `planner.llm_timeout_s` in AiSearch.yaml (default 5 s) and redeploy.
- ⚠️ **Max latency spike** on `/retrieve [auto, IT]`: 8932 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve [auto, summarize=false]`: p99 (7700 ms) is 17× the p50 (450 ms). Likely cause: LLM call (QueryUnderstanding + RetrievalPlanner) has no timeout — slow Gemini responses hang the thread. Fix: set `planner.llm_timeout_s` in AiSearch.yaml (default 5 s) and redeploy.
- ⚠️ **Max latency spike** on `/retrieve [auto, summarize=false]`: 7666 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/fulltext [HR]`: p99 (4000 ms) is 12× the p50 (330 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/fulltext [HR]`: 4145 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/fulltext [IT]`: p99 (3400 ms) is 10× the p50 (340 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/fulltext [IT]`: 4360 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/hybrid [HR]`: p99 (7900 ms) is 17× the p50 (460 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/hybrid [HR]`: 9698 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/hybrid [IT]`: p99 (7900 ms) is 17× the p50 (460 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/hybrid [IT]`: 9664 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/hybrid [top_k var]`: p99 (8700 ms) is 19× the p50 (470 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/hybrid [top_k var]`: 9733 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/vector [HR]`: p99 (7700 ms) is 17× the p50 (450 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/vector [HR]`: 9425 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/vector [IT]`: p99 (7700 ms) is 17× the p50 (450 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/vector [IT]`: 8346 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ❌ **High tail latency** on `/retrieve/vector [vague]`: p99 (7800 ms) is 17× the p50 (450 ms). Likely cause: Atlas HNSW cold-cache miss or connection pool exhaustion. Fix: ensure `retrieval.max_time_ms` is set in AiSearch.yaml and raise `ATLAS_MAX_POOL` if pool is saturated.
- ⚠️ **Max latency spike** on `/retrieve/vector [vague]`: 9321 ms absolute max recorded. Consider a client-side timeout of 4 s.
- ℹ️ **Hybrid overhead over fulltext**: +128 ms at p50 (463 ms vs 335 ms). This is the RRF fusion cost — acceptable.
- ℹ️ **Auto-route NLU overhead**: +-108 ms avg (1035 ms vs 1143 ms for explicit hybrid). Each auto call runs QueryUnderstanding + RetrievalPlanner (2 LLM calls).
- ℹ️ **Summarization cost**: +369 ms avg (1404 ms with summarize vs 1035 ms without). Keep `summarize=false` by default.

    ---

    ## 9. Report Artefacts

    | File | Path |
|---|---|
| `load_report.html` | /Users/venkatesh.shanbhag/Documents/AI-Search/AiSearch/tests/load_report.html |
| `load_report_stats.csv` | /Users/venkatesh.shanbhag/Documents/AI-Search/AiSearch/tests/load_report_stats.csv |
| `load_report_stats_history.csv` | /Users/venkatesh.shanbhag/Documents/AI-Search/AiSearch/tests/load_report_stats_history.csv |
| `load_report_failures.csv` | /Users/venkatesh.shanbhag/Documents/AI-Search/AiSearch/tests/load_report_failures.csv |
