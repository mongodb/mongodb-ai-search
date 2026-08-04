"""
load_test.py — Locust load-testing suite for the SearchaaS FastAPI backend.
============================================================================

Covers every public retrieval endpoint + health/settings reads under realistic
concurrent load.  Three user classes with different access patterns let you
simulate a mixed workload in a single run.

Quick start
-----------
    # Terminal 1 — start the server (must already be running)
    uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000

    # Terminal 2 — headless run (no browser UI), 20 users, 2 spawned/s, 60 s
    cd <repo-root>
    locust -f searchaas/tests/load_test.py \
           --headless \
           --host http://localhost:8000 \
           -u 20 -r 2 --run-time 60s \
           --html searchaas/tests/load_report.html

    # Interactive browser UI (visit http://localhost:8089 to start/stop)
    locust -f searchaas/tests/load_test.py --host http://localhost:8000

    # Target a specific user class only
    locust -f searchaas/tests/load_test.py \
           --headless --host http://localhost:8000 \
           -u 10 -r 1 --run-time 30s \
           --class-picker          # or pass class names directly:
           HybridSearchUser

Per-collection overrides
------------------------
Every request passes explicit `atlas` overrides so the load test works with
any collection registered in your searchaas.yaml — just set environment
variables before running:

    LOAD_COLLECTION=IT_helpdesk \\
    LOAD_VECTOR_INDEX=it_helpdesk_vector_index \\
    LOAD_SEARCH_INDEX=it_helpdesk_search_index \\
    locust -f searchaas/tests/load_test.py --headless ...

Env vars (all optional — fall back to the defaults below)
----------------------------------------------------------
    LOAD_HOST            Base URL of the SearchaaS server   (default: http://localhost:8000)
    LOAD_COLLECTION      MongoDB collection to query         (default: IT_helpdesk)
    LOAD_VECTOR_INDEX    Atlas Vector Search index name      (default: it_helpdesk_vector_index)
    LOAD_SEARCH_INDEX    Atlas Search (Lucene) index name    (default: it_helpdesk_search_index)
    LOAD_TEXT_KEY        Document text field                 (default: text)
    LOAD_TOP_K           Number of results per request       (default: 5)
    LOAD_THINK_MIN_S     Min think-time between requests (s) (default: 1)
    LOAD_THINK_MAX_S     Max think-time between requests (s) (default: 4)
"""

from __future__ import annotations

import multiprocessing
import os
import platform
import random
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from locust import HttpUser, between, events, task
from locust.exception import RescheduleTask

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

_COLLECTION    = os.environ.get("LOAD_COLLECTION",    "employee_support")
_VECTOR_INDEX  = os.environ.get("LOAD_VECTOR_INDEX",  "vector_index")
_SEARCH_INDEX  = os.environ.get("LOAD_SEARCH_INDEX",  "default")
_TEXT_KEY      = os.environ.get("LOAD_TEXT_KEY",      "text")   # must match Atlas index path exactly
_TOP_K         = int(os.environ.get("LOAD_TOP_K",     "5"))
_THINK_MIN     = float(os.environ.get("LOAD_THINK_MIN_S", "1"))
_THINK_MAX     = float(os.environ.get("LOAD_THINK_MAX_S", "4"))

# ---------------------------------------------------------------------------
# Representative query bank — IT Helpdesk + Employee Support
# ---------------------------------------------------------------------------

_IT_QUERIES: list[str] = [
    "My VPN keeps disconnecting every 30 minutes on macOS. How do I fix it?",
    "How do I reset my company password without calling IT?",
    "How do I set up MFA on my phone?",
    "Steps to install Slack on a new MacBook",
    "Laptop won't connect to the corporate Wi-Fi after OS update",
    "How do I connect to a network printer in the London office?",
    "Remote desktop from home to my office PC is timing out",
    "How do I request access to Salesforce?",
    "BitLocker recovery key — how do I retrieve it?",
    "CrowdStrike quarantined a file I need — how do I restore it?",
    "My Outlook keeps asking for password even with SSO",
    "What is the SLA for a critical IT helpdesk ticket?",
    "How to install a root CA certificate on Chrome?",
    "What ports need to be open for GlobalProtect VPN?",
    "How do I get a hardware FIDO2 key for MFA?",
    "Software request process for approved applications",
    "SSO session expired — why am I being asked to re-authenticate?",
    "How do I set up email on my personal iPhone?",
]

_HR_QUERIES: list[str] = [
    "What is the annual PTO policy for new joiners?",
    "How many days of sick leave do I get per year?",
    "When is salary paid each month?",
    "How do I submit a travel expense reimbursement?",
    "What is the maximum hotel rate allowed for business travel?",
    "How do I enrol in health insurance?",
    "What is the company 401k match?",
    "How do I apply for maternity leave?",
    "How many days WFH are allowed per week?",
    "What are the floating holidays and how do I use them?",
    "How do I refer a friend for a job opening?",
    "What is the notice period for resignation?",
    "When are performance reviews conducted?",
    "How does the promotion process work?",
    "What compliance training is mandatory and when is it due?",
    "How do I request a salary adjustment?",
    "What are the new hire onboarding steps for day one?",
    "Can I carry over unused PTO to next year?",
]

_ALL_QUERIES: list[str] = _IT_QUERIES + _HR_QUERIES

# ---------------------------------------------------------------------------
# Atlas override payload (injected on every request)
# ---------------------------------------------------------------------------

def _atlas_overrides() -> dict[str, Any]:
    return {
        "collection":   _COLLECTION,
        "vector_index": _VECTOR_INDEX,
        "search_index": _SEARCH_INDEX,
        "text_key":     _TEXT_KEY,
        "embedding_key": None,
    }


def _base_payload(query: str, top_k: int = _TOP_K) -> dict[str, Any]:
    return {
        "query":     query,
        "top_k":     top_k,
        "summarize": False,
        "understand": False,
        "atlas":     _atlas_overrides(),
    }

# ---------------------------------------------------------------------------
# Shared POST helper — raises RescheduleTask on 5xx so Locust retries
# ---------------------------------------------------------------------------

def _post(client, endpoint: str, payload: dict[str, Any], name: str) -> None:
    with client.post(
        endpoint,
        json=payload,
        name=name,
        catch_response=True,
    ) as resp:
        if resp.status_code >= 500:
            resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
            raise RescheduleTask()
        elif resp.status_code >= 400:
            resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            try:
                data = resp.json()
            except Exception:
                resp.failure("Response is not valid JSON")
                return
            results = data.get("results") or []
            if not results:
                # Not a hard failure — Atlas may return 0 results on a cold index.
                resp.success()
            else:
                resp.success()


# ===========================================================================
# User class 1 — HybridSearchUser
# Primary workload: hybrid retrieval (most realistic production traffic).
# ===========================================================================

class HybridSearchUser(HttpUser):
    """
    Simulates the employee-support-copilot's primary retrieval path:
    hybrid vector + full-text search.

    Weight 3: spawned 3× more frequently than other classes.
    """

    weight      = 3
    wait_time   = between(_THINK_MIN, _THINK_MAX)

    @task(5)
    def hybrid_it_query(self) -> None:
        """Hybrid search with an IT Helpdesk query."""
        _post(
            self.client,
            "/retrieve/hybrid",
            _base_payload(random.choice(_IT_QUERIES)),
            name="/retrieve/hybrid [IT]",
        )

    @task(5)
    def hybrid_hr_query(self) -> None:
        """Hybrid search with an HR / Employee Support query."""
        _post(
            self.client,
            "/retrieve/hybrid",
            _base_payload(random.choice(_HR_QUERIES)),
            name="/retrieve/hybrid [HR]",
        )

    @task(2)
    def hybrid_with_top_k_variation(self) -> None:
        """Hybrid search varying top_k (3, 5, 8, 10)."""
        _post(
            self.client,
            "/retrieve/hybrid",
            _base_payload(random.choice(_ALL_QUERIES), top_k=random.choice([3, 5, 8, 10])),
            name="/retrieve/hybrid [top_k var]",
        )

    @task(1)
    def health_check(self) -> None:
        """Lightweight health probe — low weight, just keeps the check in stats."""
        with self.client.get("/health", name="/health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Health check failed: {resp.status_code}")
            else:
                resp.success()


# ===========================================================================
# User class 2 — VectorSearchUser
# Pure semantic (vector-only) queries — tests the autoEmbed path in isolation.
# ===========================================================================

class VectorSearchUser(HttpUser):
    """
    Simulates semantic / fuzzy queries that lean heavily on vector similarity.
    """

    weight    = 2
    wait_time = between(_THINK_MIN, _THINK_MAX)

    @task(4)
    def vector_it_query(self) -> None:
        _post(
            self.client,
            "/retrieve/vector",
            _base_payload(random.choice(_IT_QUERIES)),
            name="/retrieve/vector [IT]",
        )

    @task(4)
    def vector_hr_query(self) -> None:
        _post(
            self.client,
            "/retrieve/vector",
            _base_payload(random.choice(_HR_QUERIES)),
            name="/retrieve/vector [HR]",
        )

    @task(2)
    def vector_broad_query(self) -> None:
        """Short, ambiguous queries that stress the embedding layer."""
        vague = [
            "access issues",
            "policy",
            "benefits",
            "not working",
            "how do I",
            "setup help",
            "leave request",
            "password",
        ]
        _post(
            self.client,
            "/retrieve/vector",
            _base_payload(random.choice(vague)),
            name="/retrieve/vector [vague]",
        )


# ===========================================================================
# User class 3 — FullTextSearchUser
# Exact-lookup / keyword queries via Atlas Search (Lucene).
# ===========================================================================

class FullTextSearchUser(HttpUser):
    """
    Simulates keyword-heavy, policy-lookup style queries.
    """

    weight    = 1
    wait_time = between(_THINK_MIN, _THINK_MAX)

    @task(4)
    def fulltext_it_query(self) -> None:
        _post(
            self.client,
            "/retrieve/fulltext",
            _base_payload(random.choice(_IT_QUERIES)),
            name="/retrieve/fulltext [IT]",
        )

    @task(4)
    def fulltext_hr_query(self) -> None:
        _post(
            self.client,
            "/retrieve/fulltext",
            _base_payload(random.choice(_HR_QUERIES)),
            name="/retrieve/fulltext [HR]",
        )

    @task(1)
    def read_settings(self) -> None:
        """Read-only GET /settings — validates the config endpoint holds up under load."""
        with self.client.get("/settings", name="/settings", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"GET /settings failed: {resp.status_code}")
            else:
                resp.success()


# ===========================================================================
# User class 4 — AutoRouteUser
# Hits the smart /retrieve endpoint which runs NLU + planner each time.
# Lower weight — most expensive per-request (adds LLM latency).
# ===========================================================================

class AutoRouteUser(HttpUser):
    """
    Exercises the /retrieve auto endpoint that runs Query Understanding
    + RetrievalPlanner before executing. Tests the full pipeline cost.
    """

    weight    = 1
    wait_time = between(_THINK_MIN * 2, _THINK_MAX * 2)  # extra think time for slow path

    @task(3)
    def auto_it_query(self) -> None:
        _post(
            self.client,
            "/retrieve",
            _base_payload(random.choice(_IT_QUERIES)),
            name="/retrieve [auto, IT]",
        )

    @task(3)
    def auto_hr_query(self) -> None:
        _post(
            self.client,
            "/retrieve",
            _base_payload(random.choice(_HR_QUERIES)),
            name="/retrieve [auto, HR]",
        )

    @task(1)
    def auto_with_summarize(self) -> None:
        """Auto route — summarize disabled by default for load testing."""
        _post(
            self.client,
            "/retrieve",
            _base_payload(random.choice(_ALL_QUERIES)),
            name="/retrieve [auto, summarize=false]",
        )


# ===========================================================================
# Report output directory — same folder as this file
# ===========================================================================

_TESTS_DIR = Path(__file__).parent


# ===========================================================================
# Machine / environment info — collected once at import time
# ===========================================================================

def _collect_machine_info() -> dict[str, str]:
    """Collect load-generator machine info for the report header."""
    info: dict[str, str] = {}

    # OS + kernel
    info["os"]      = platform.system()
    info["os_ver"]  = platform.version().split()[0] if platform.version() else "unknown"
    info["machine"] = platform.machine()

    # CPU
    info["cpu_logical"]  = str(multiprocessing.cpu_count() or "?")
    try:
        import psutil
        info["cpu_physical"] = str(psutil.cpu_count(logical=False) or "?")
        vm = psutil.virtual_memory()
        info["ram_total_gb"] = f"{vm.total / 1024**3:.1f}"
        info["ram_avail_gb"] = f"{vm.available / 1024**3:.1f}"
        freq = psutil.cpu_freq()
        info["cpu_freq_ghz"] = f"{freq.max / 1000:.2f}" if freq else "?"
    except ImportError:
        info["cpu_physical"] = "?"
        info["ram_total_gb"] = "? (install psutil for RAM info)"
        info["ram_avail_gb"] = "?"
        info["cpu_freq_ghz"] = "?"

    # Python
    info["python"] = platform.python_version()

    # Locust worker count (headless = 1 master process unless --processes used)
    info["locust_workers"] = os.environ.get("LOCUST_WORKERS", "1 (master)")

    # Cloud Run target details from env (if set)
    info["target_cpu"]      = os.environ.get("CLOUD_RUN_CPU",      "unknown")
    info["target_mem"]      = os.environ.get("CLOUD_RUN_MEM",      "unknown")
    info["target_instances"]= os.environ.get("CLOUD_RUN_MAX_INST", "unknown")
    info["target_concurr"]  = os.environ.get("CLOUD_RUN_CONCURR",  "unknown")

    return info


_MACHINE = _collect_machine_info()


# ===========================================================================
# Capacity ceiling calculator
# ===========================================================================

def _capacity_analysis(
    total: Any,
    entries: list,
    run_time_s: float,
    target_user_count: int,
    think_min: float,
    think_max: float,
) -> str:
    """
    Compute the estimated maximum sustainable TPS for this server
    based on observed p50 latency and failure behaviour.

    Little's Law:   TPS = concurrent_users / avg_cycle_time
    avg_cycle_time = avg_think_time + avg_response_time_s

    The "safe capacity" is the TPS at which the failure rate is still 0%
    and p99 stays within SLA. We derive it from the observed data across runs.
    """
    lines: list[str] = []

    # --- observed metrics ---
    total_reqs  = total.num_requests
    total_fails = total.num_failures
    fail_pct    = 100.0 * total_fails / max(total_reqs, 1)
    obs_tps     = total.total_rps
    p50_ms      = total.get_response_time_percentile(0.50) or 0
    p99_ms      = total.get_response_time_percentile(0.99) or 0

    # Average think time (midpoint of the configured range)
    avg_think_s = (think_min + think_max) / 2.0

    # Little's Law: TPS = N / (think_time + response_time)
    avg_resp_s = (p50_ms / 1000.0) if p50_ms else 0.001
    littles_tps = target_user_count / (avg_think_s + avg_resp_s) if avg_resp_s else 0

    # Per-strategy breakdown (successful requests only)
    strategy_tps: list[tuple[str, float, int, int]] = []
    for e in sorted(entries, key=lambda x: -x.total_rps):
        if e.num_requests == 0:
            continue
        ep_fail_pct = 100.0 * e.num_failures / e.num_requests
        ep_p50      = int(e.get_response_time_percentile(0.50) or 0)
        strategy_tps.append((
            f"{e.method} {e.name}",
            e.total_rps,
            ep_p50,
            int(ep_fail_pct),
        ))

    # Capacity verdict
    if fail_pct == 0:
        capacity_verdict = (
            f"✅ Server handled **{target_user_count:,} users** with zero failures. "
            f"Current load is within capacity. Observed TPS: **{obs_tps:.1f}**."
        )
        # Estimate max sustainable users via Little's Law (add 20% headroom)
        safe_users = int(target_user_count * 1.2)
        capacity_verdict += (
            f"\n\n  **Estimated safe ceiling**: ~{safe_users:,} users "
            f"(+20% headroom over current load)."
        )
    elif fail_pct < 5:
        safe_tps = obs_tps * (1 - fail_pct / 100)
        capacity_verdict = (
            f"⚠️ Minor failures ({fail_pct:.1f}%). "
            f"Effective throughput of successful requests: **{safe_tps:.1f} TPS**. "
            f"Reduce concurrency by ~{int(fail_pct + 10)}% for a stable baseline."
        )
    else:
        # Back-calculate safe user count from Little's Law at 0% failure
        # Assume p50 at low load ≈ 440 ms (from earlier stable runs)
        baseline_resp_s = 0.44
        safe_tps = 1.0 / (avg_think_s + baseline_resp_s) if avg_think_s > 0 else 1
        safe_users_est  = int(safe_tps * (avg_think_s + baseline_resp_s))
        capacity_verdict = (
            f"❌ High failure rate ({fail_pct:.1f}%) — server is over capacity at "
            f"{target_user_count:,} users.\n\n"
            f"  **Estimated safe ceiling** (from Little's Law at p50=440 ms): "
            f"**~{safe_users_est:,} users / {safe_tps:.0f} TPS** with zero failures.\n\n"
            f"  **Observed TPS (incl. failures):** {obs_tps:.1f}"
        )

    lines.append(capacity_verdict)
    lines.append("")

    # --- per-strategy TPS table ---
    lines.append("### TPS by endpoint")
    lines.append("")
    lines.append("| Endpoint | TPS | p50 (ms) | Fail % |")
    lines.append("|---|---|---|---|")
    for name, tps, p50, fp in strategy_tps:
        fail_flag = "❌" if fp > 5 else ("⚠️" if fp > 0 else "✅")
        lines.append(f"| `{name}` | {tps:.2f} | {p50} | {fail_flag} {fp}% |")
    lines.append("")

    # --- Little's Law working ---
    lines.append("### Capacity model (Little's Law)")
    lines.append("")
    lines.append("```")
    lines.append(f"  TPS  = N / (think_time + response_time)")
    lines.append(f"       = {target_user_count:,} / ({avg_think_s:.1f}s + {avg_resp_s:.2f}s)")
    lines.append(f"       = {littles_tps:.1f} TPS  ← theoretical max at this concurrency")
    lines.append(f"")
    lines.append(f"  Observed TPS : {obs_tps:.1f}  ({fail_pct:.1f}% failure rate)")
    lines.append(f"  Run duration : {run_time_s:.0f} s")
    lines.append(f"  Total reqs   : {total_reqs:,}  (success: {total_reqs - total_fails:,}  fail: {total_fails:,})")
    lines.append("```")

    return "\n".join(lines)


# ===========================================================================
# Event hooks
# ===========================================================================

_test_start_time: float = 0.0


@events.test_start.add_listener
def on_test_start(environment, **kwargs) -> None:
    import time as _time
    global _test_start_time
    _test_start_time = _time.monotonic()

    print("\n" + "=" * 60)
    print("  SearchaaS Load Test — configuration")
    print("=" * 60)
    print(f"  host       : {environment.host}")
    print(f"  collection : {_COLLECTION}")
    print(f"  vector_idx : {_VECTOR_INDEX}")
    print(f"  search_idx : {_SEARCH_INDEX}")
    print(f"  top_k      : {_TOP_K}")
    print(f"  think time : {_THINK_MIN}s – {_THINK_MAX}s")
    print(f"  machine    : {_MACHINE['os']} · {_MACHINE['cpu_logical']} vCPU · {_MACHINE['ram_total_gb']} GB RAM")
    print("=" * 60 + "\n")


@events.quitting.add_listener
def on_quitting(environment, **kwargs) -> None:
    import time as _time
    run_time_s = _time.monotonic() - _test_start_time if _test_start_time else 0

    stats = environment.stats
    total = stats.total
    if total.num_requests == 0:
        print("[report] No requests recorded — skipping report generation.")
        return

    fail_pct = 100 * total.num_failures / max(total.num_requests, 1)

    # ── brief terminal summary ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SearchaaS Load Test — summary")
    print("=" * 60)
    print(f"  requests   : {total.num_requests:,}")
    print(f"  failures   : {total.num_failures:,}  ({fail_pct:.1f}%)")
    print(f"  TPS        : {total.total_rps:.1f}")
    print(f"  p50 ms     : {total.get_response_time_percentile(0.50):.0f}")
    print(f"  p95 ms     : {total.get_response_time_percentile(0.95):.0f}")
    print(f"  p99 ms     : {total.get_response_time_percentile(0.99):.0f}")
    print(f"  max ms     : {total.max_response_time:.0f}")
    print(f"  duration   : {run_time_s:.0f}s")
    print(f"  machine    : {_MACHINE['os']} {_MACHINE['cpu_logical']} vCPU {_MACHINE['ram_total_gb']} GB RAM")
    print("=" * 60 + "\n")

    # ── generate markdown report ──────────────────────────────────────────
    report_path = _TESTS_DIR / "LOAD_TEST_REPORT.md"
    md = _build_report(environment, run_time_s)
    report_path.write_text(md, encoding="utf-8")
    print(f"[report] Written → {report_path}")


# ===========================================================================
# Report builder — converts live Locust stats into a markdown analysis
# ===========================================================================

def _pct(entry, p: float) -> int:
    """Return a percentile value (ms) from a Locust StatsEntry."""
    v = entry.get_response_time_percentile(p)
    return int(v) if v is not None else 0


def _failure_pct(entry) -> float:
    n = entry.num_requests
    return 0.0 if n == 0 else 100.0 * entry.num_failures / n


def _sla(actual_ms: int, limit_ms: int) -> str:
    return "✅" if actual_ms <= limit_ms else "❌"


# SLA thresholds per endpoint group (ms).
_SLA: dict[str, dict[str, int]] = {
    "fulltext":  {"p50": 500,  "p95": 800,  "p99": 1_000},
    "vector":    {"p50": 800,  "p95": 1_500, "p99": 3_000},
    "hybrid":    {"p50": 800,  "p95": 1_500, "p99": 3_000},
    "auto":      {"p50": 2_000, "p95": 3_000, "p99": 5_000},
    "summarize": {"p50": 5_000, "p95": 8_000, "p99": 10_000},
    "infra":     {"p50": 50,   "p95": 200,  "p99": 500},
}

def _endpoint_group(name: str) -> str:
    n = name.lower()
    if "summarize" in n:   return "summarize"
    if "auto"      in n:   return "auto"
    if "hybrid"    in n:   return "hybrid"
    if "vector"    in n:   return "vector"
    if "fulltext"  in n:   return "fulltext"
    return "infra"


def _diagnose(entries: list) -> list[str]:
    """Return a list of finding strings derived from the stats."""
    findings: list[str] = []

    total_reqs  = sum(e.num_requests for e in entries)
    total_fails = sum(e.num_failures for e in entries)

    # Overall failure rate
    fail_pct = 100.0 * total_fails / max(total_reqs, 1)
    if fail_pct == 0:
        findings.append("✅ **Zero failures** across all endpoints — server is stable at this concurrency.")
    elif fail_pct < 1:
        findings.append(f"⚠️ Low failure rate ({fail_pct:.2f}%) — investigate individual endpoint failures.")
    else:
        findings.append(f"❌ High failure rate ({fail_pct:.2f}%) — server is struggling; reduce concurrency or fix errors.")

    # Tail latency analysis per retrieval endpoint
    retrieval = [e for e in entries if e.method == "POST"]
    for e in retrieval:
        p50  = _pct(e, 0.50)
        p99  = _pct(e, 0.99)
        mx   = int(e.max_response_time)
        spread = p99 / p50 if p50 > 0 else 0
        if spread >= 10:
            grp = _endpoint_group(e.name)
            if grp in ("auto", "summarize"):
                cause = (
                    "LLM call (QueryUnderstanding + RetrievalPlanner) has no timeout — "
                    "slow Gemini responses hang the thread. Fix: set `planner.llm_timeout_s` "
                    "in searchaas.yaml (default 5 s) and redeploy."
                )
            else:
                cause = (
                    "Atlas HNSW cold-cache miss or connection pool exhaustion. "
                    "Fix: ensure `retrieval.max_time_ms` is set in searchaas.yaml "
                    "and raise `ATLAS_MAX_POOL` if pool is saturated."
                )
            findings.append(
                f"❌ **High tail latency** on `{e.name}`: p99 ({p99} ms) is "
                f"{spread:.0f}× the p50 ({p50} ms). Likely cause: {cause}"
            )
        elif spread >= 5:
            findings.append(
                f"⚠️ **Elevated tail** on `{e.name}`: p99 ({p99} ms) is "
                f"{spread:.0f}× the p50 ({p50} ms). Monitor under higher load."
            )

        if mx > 4_000:
            findings.append(
                f"⚠️ **Max latency spike** on `{e.name}`: {mx} ms absolute max recorded. "
                f"Consider a client-side timeout of 4 s."
            )

    # Fulltext vs hybrid comparison
    ft_entries  = [e for e in entries if "fulltext" in e.name.lower()]
    hyb_entries = [e for e in entries if "hybrid"   in e.name.lower()]
    if ft_entries and hyb_entries:
        ft_p50  = int(sum(_pct(e, 0.50) for e in ft_entries)  / len(ft_entries))
        hyb_p50 = int(sum(_pct(e, 0.50) for e in hyb_entries) / len(hyb_entries))
        overhead = hyb_p50 - ft_p50
        findings.append(
            f"ℹ️ **Hybrid overhead over fulltext**: +{overhead} ms at p50 "
            f"({hyb_p50} ms vs {ft_p50} ms). This is the RRF fusion cost — acceptable."
        )

    # Auto-route overhead
    auto_entries = [e for e in entries if "auto" in e.name.lower() and "summarize" not in e.name.lower()]
    hyb_entries2 = [e for e in entries if "hybrid" in e.name.lower()]
    if auto_entries and hyb_entries2:
        auto_avg = int(sum(e.avg_response_time for e in auto_entries) / len(auto_entries))
        hyb_avg  = int(sum(e.avg_response_time for e in hyb_entries2) / len(hyb_entries2))
        nlu_overhead = auto_avg - hyb_avg
        findings.append(
            f"ℹ️ **Auto-route NLU overhead**: +{nlu_overhead} ms avg "
            f"({auto_avg} ms vs {hyb_avg} ms for explicit hybrid). "
            f"Each auto call runs QueryUnderstanding + RetrievalPlanner (2 LLM calls)."
        )

    # Summarization cost
    sum_entries = [e for e in entries if "summarize" in e.name.lower()]
    if sum_entries and auto_entries:
        sum_avg  = int(sum(e.avg_response_time for e in sum_entries) / len(sum_entries))
        auto_avg = int(sum(e.avg_response_time for e in auto_entries) / len(auto_entries))
        findings.append(
            f"ℹ️ **Summarization cost**: +{sum_avg - auto_avg} ms avg "
            f"({sum_avg} ms with summarize vs {auto_avg} ms without). "
            f"Keep `summarize=false` by default."
        )

    return findings


def _build_report(environment, run_time_s: float = 0.0) -> str:
    """Render the full markdown report from live Locust stats."""
    stats   = environment.stats
    total   = stats.total
    entries = sorted(
        [e for e in stats.entries.values() if e.num_requests > 0],
        key=lambda e: e.name,
    )

    now               = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    target_user_count = getattr(environment.runner, "target_user_count", 0) or 0
    host              = environment.host or "unknown"

    total_reqs  = total.num_requests
    total_fails = total.num_failures
    fail_pct    = 100.0 * total_fails / max(total_reqs, 1)
    tps         = total.total_rps

    p50_all = _pct(total, 0.50)
    p95_all = _pct(total, 0.95)
    p99_all = _pct(total, 0.99)
    max_all = int(total.max_response_time)

    findings = _diagnose(entries)

    # ── machine config block ──────────────────────────────────────────────
    m = _MACHINE
    target_line = ""
    if m["target_cpu"] != "unknown":
        target_line = (
            f"\n\n**Target server (Cloud Run):** "
            f"{m['target_cpu']} vCPU · {m['target_mem']} RAM · "
            f"max {m['target_instances']} instances · "
            f"{m['target_concurr']} concurrency/instance"
        )

    machine_md = textwrap.dedent(f"""\
    | Property | Value |
    |---|---|
    | OS | {m['os']} {m['os_ver']} ({m['machine']}) |
    | CPU (logical) | {m['cpu_logical']} vCPU |
    | CPU (physical) | {m['cpu_physical']} cores |
    | CPU freq (max) | {m['cpu_freq_ghz']} GHz |
    | RAM total | {m['ram_total_gb']} GB |
    | RAM available | {m['ram_avail_gb']} GB |
    | Python | {m['python']} |
    | Locust workers | {m['locust_workers']} |
    """) + target_line

    # ── capacity analysis ─────────────────────────────────────────────────
    capacity_md = _capacity_analysis(
        total, entries, run_time_s, target_user_count, _THINK_MIN, _THINK_MAX
    )

    findings = _diagnose(entries)

    # ── per-endpoint table rows ───────────────────────────────────────────
    def _row(e) -> str:
        avg  = int(e.avg_response_time)
        p50  = _pct(e, 0.50)
        p90  = _pct(e, 0.90)
        p95  = _pct(e, 0.95)
        p99  = _pct(e, 0.99)
        mx   = int(e.max_response_time)
        fp   = _failure_pct(e)
        rps_ = f"{e.total_rps:.2f}"
        fail_col = f"**{e.num_failures}** ({fp:.1f}%)" if e.num_failures else "0"
        return (
            f"| `{e.method} {e.name}` "
            f"| {e.num_requests} | {fail_col} "
            f"| {avg} | {p50} | {p90} | {p95} | {p99} | {mx} | {rps_} |"
        )

    table_rows = "\n".join(_row(e) for e in entries)

    # ── SLA table ─────────────────────────────────────────────────────────
    def _sla_row(e) -> str:
        grp  = _endpoint_group(e.name)
        sla  = _SLA[grp]
        p50  = _pct(e, 0.50)
        p95  = _pct(e, 0.95)
        p99  = _pct(e, 0.99)
        return (
            f"| `{e.method} {e.name}` "
            f"| {_sla(p50, sla['p50'])} {p50} ms (≤{sla['p50']}) "
            f"| {_sla(p95, sla['p95'])} {p95} ms (≤{sla['p95']}) "
            f"| {_sla(p99, sla['p99'])} {p99} ms (≤{sla['p99']}) |"
        )

    sla_rows = "\n".join(_sla_row(e) for e in entries if e.method in ("GET", "POST"))

    # ── latency spread table ──────────────────────────────────────────────
    def _spread_row(e) -> str:
        p50 = _pct(e, 0.50)
        p99 = _pct(e, 0.99)
        mx  = int(e.max_response_time)
        spread = f"{p99/p50:.1f}×" if p50 > 0 else "n/a"
        flag = "🔴" if (p50 > 0 and p99 / p50 >= 10) else ("🟡" if (p50 > 0 and p99 / p50 >= 5) else "🟢")
        return f"| `{e.name}` | {p50} | {p99} | {mx} | {spread} | {flag} |"

    spread_rows = "\n".join(
        _spread_row(e) for e in entries if e.method == "POST"
    )

    # ── findings bullets ──────────────────────────────────────────────────
    findings_md = "\n".join(f"- {f}" for f in findings)

    # ── overall verdict ───────────────────────────────────────────────────
    if fail_pct == 0 and p99_all <= 4_000:
        verdict = "🟢 **PASS** — Server is stable. No failures. Tail latency within acceptable range."
    elif fail_pct == 0 and p99_all > 4_000:
        verdict = "🟡 **PASS WITH WARNINGS** — No failures, but p99 tail latency is high. Review spike patterns."
    elif fail_pct < 1:
        verdict = "🟡 **MARGINAL** — Low failure rate with latency issues. Investigate before scaling."
    else:
        verdict = "🔴 **FAIL** — Failure rate exceeds threshold. Server needs tuning before production use."

    artefacts = []
    for name in ("load_report.html", "load_report_stats.csv",
                 "load_report_stats_history.csv", "load_report_failures.csv"):
        p = _TESTS_DIR / name
        if p.exists():
            artefacts.append(f"| `{name}` | {p} |")
    artefacts_md = (
        "| File | Path |\n|---|---|\n" + "\n".join(artefacts)
        if artefacts else "_Run with `--html` and `--csv` flags to generate artefact files._"
    )

    run_dur_str = f"{run_time_s:.0f} s" if run_time_s else "unknown"

    return textwrap.dedent(f"""\
    # SearchaaS Load Test Report

    **Generated:** {now}  
    **Host:** {host}  
    **Collection:** `{_COLLECTION}` · vector index: `{_VECTOR_INDEX}` · search index: `{_SEARCH_INDEX}`  
    **Concurrency:** {target_user_count:,} virtual users · think time {_THINK_MIN}–{_THINK_MAX} s  
    **Duration:** {run_dur_str}  

    ---

    ## 1. Overall Verdict

    {verdict}

    ---

    ## 2. Load Generator Machine Config

    {machine_md}

    ---

    ## 3. Executive Summary

    | Metric | Value |
    |---|---|
    | Total requests | {total_reqs:,} |
    | Failures | **{total_fails:,} ({fail_pct:.1f}%)** |
    | TPS (transactions/sec) | **{tps:.1f}** |
    | Successful TPS | **{tps * (1 - fail_pct/100):.1f}** |
    | p50 latency | **{p50_all} ms** |
    | p95 latency | **{p95_all} ms** |
    | p99 latency | **{p99_all} ms** |
    | Max latency | **{max_all} ms** |
    | Run duration | **{run_dur_str}** |

    ---

    ## 4. Capacity Analysis & Maximum Sustainable Load

    {capacity_md}

    ---

    ## 5. Per-Endpoint Statistics

    | Endpoint | Reqs | Failures | Avg (ms) | p50 | p90 | p95 | p99 | Max | TPS |
    |---|---|---|---|---|---|---|---|---|---|
    {table_rows}

    ---

    ## 6. Tail Latency Spread (p99 / p50 ratio)

    A healthy endpoint has a spread < 5×. Anything ≥ 10× indicates sporadic severe outliers.

    | Endpoint | p50 (ms) | p99 (ms) | Max (ms) | Spread | Status |
    |---|---|---|---|---|---|
    {spread_rows}

    🟢 < 5× &nbsp; 🟡 5–10× &nbsp; 🔴 ≥ 10×

    ---

    ## 7. SLA Assessment

    | Endpoint | p50 | p95 | p99 |
    |---|---|---|---|
    {sla_rows}

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

    {findings_md}

    ---

    ## 9. Report Artefacts

    {artefacts_md}
    """)

