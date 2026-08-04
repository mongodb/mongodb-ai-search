"""
load_test_retail.py — Locust load-testing suite for the kaggle_dataset.retail collection.
==========================================================================================

Collection:  kaggle_dataset.retail  (50,425 docs)
Fields:      Text (autoEmbed path, voyage-4), Category (filter)
Indexes:
  vector_index  — vectorSearch, autoEmbed path='Text', model=voyage-4
  default       — Atlas Search (Lucene), dynamic mapping

Four user classes covering every retrieval strategy:
  RetailHybridUser    — hybrid vector + fulltext  (weight 3, primary workload)
  RetailVectorUser    — pure semantic / vector     (weight 2)
  RetailFulltextUser  — keyword / Lucene           (weight 1)
  RetailAutoUser      — NLU + planner auto-route   (weight 1, slowest)

Quick start
-----------
    # Terminal 1 — start the server
    uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000

    # Terminal 2 — headless, 30 users, 90 s, HTML + CSV report
    cd <repo-root>
    locust -f searchaas/tests/load_test_retail.py \\
           --headless \\
           --host http://localhost:8000 \\
           -u 30 -r 3 --run-time 90s \\
           --html searchaas/tests/load_report.html \\
           --csv  searchaas/tests/load_report

    # Interactive browser UI at http://localhost:8089
    locust -f searchaas/tests/load_test_retail.py --host http://localhost:8000

    # Cloud Run target
    locust -f searchaas/tests/load_test_retail.py \\
           --headless \\
           --host https://searchaas-api-<hash>.run.app \\
           -u 50 -r 5 --run-time 120s \\
           --html searchaas/tests/load_report.html \\
           --csv  searchaas/tests/load_report

Env vars (all optional)
-----------------------
    LOAD_HOST         Server base URL              (default: http://localhost:8000)
    LOAD_TOP_K        Results per request           (default: 5)
    LOAD_THINK_MIN_S  Min think-time in seconds     (default: 1)
    LOAD_THINK_MAX_S  Max think-time in seconds     (default: 4)
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
# Fixed collection config — kaggle_dataset.retail
# ---------------------------------------------------------------------------

_COLLECTION   = "retail"
_VECTOR_INDEX = "vector_index"
_SEARCH_INDEX = "default"
_TEXT_KEY     = "Text"          # MUST match the Atlas autoEmbed index path exactly
_TOP_K        = int(os.environ.get("LOAD_TOP_K",        "5"))
_THINK_MIN    = float(os.environ.get("LOAD_THINK_MIN_S", "1"))
_THINK_MAX    = float(os.environ.get("LOAD_THINK_MAX_S", "4"))

# ---------------------------------------------------------------------------
# Retail query bank — realistic product-search and discovery queries
# spanning all four categories (Books, Clothing & Accessories, Electronics,
# Household) with a mix of semantic, keyword, and vague intents.
# ---------------------------------------------------------------------------

_BOOKS_QUERIES: list[str] = [
    "self help books for personal growth and motivation",
    "yoga and mindfulness guide for beginners",
    "engineering and technology textbooks",
    "fiction novels with adventure and mystery themes",
    "books on machine learning and artificial intelligence",
    "biography of famous scientists and inventors",
    "children's educational books for early learning",
    "cooking and recipe books for healthy meals",
    "history books about ancient civilisations",
    "philosophy books for everyday life",
    "business strategy and leadership books",
    "books on finance and investing for beginners",
    "spiritual guide for inner peace",
    "programming books for Python developers",
    "best sellers in Indian literature",
]

_CLOTHING_QUERIES: list[str] = [
    "summer dresses for women casual wear",
    "men's formal shirts for office",
    "kids winter jackets and warm clothing",
    "ethnic wear sarees and kurtas for women",
    "sports and gym wear for men",
    "baby clothes newborn outfits",
    "woollen sweaters for cold weather",
    "trendy jeans and trousers for teenagers",
    "women's handbags and accessories",
    "comfortable cotton t-shirts",
    "school uniforms for children",
    "wedding and party wear for women",
    "running shoes and athletic footwear",
    "traditional Indian kurta pyjama set for men",
    "raincoat and waterproof jacket",
]

_ELECTRONICS_QUERIES: list[str] = [
    "laptop adapter and charger compatible with Dell",
    "wireless bluetooth earphones and headphones",
    "smartphone accessories cases and screen protectors",
    "USB hub and cable for laptop",
    "portable power bank high capacity",
    "gaming mouse and mechanical keyboard",
    "LED monitor for home office",
    "tablet and iPad accessories",
    "security camera for home surveillance",
    "smart home devices Amazon Echo",
    "printer ink cartridge replacement",
    "external hard drive for data backup",
    "HDMI cable 4K display",
    "fast charging adapter for mobile",
    "webcam for video conferencing work from home",
]

_HOUSEHOLD_QUERIES: list[str] = [
    "wall art and motivational posters for office",
    "kitchen storage containers and organisers",
    "bedsheets and pillow covers cotton",
    "decorative photo frames for living room",
    "bathroom accessories soap dispenser and towel rack",
    "LED fairy lights for decoration",
    "non-stick cookware set frying pan",
    "garden tools and outdoor accessories",
    "air freshener and room fragrance diffuser",
    "vacuum cleaner for home use",
    "plastic storage boxes for wardrobe",
    "wooden furniture small table and chair",
    "curtains and blinds for bedroom",
    "cleaning supplies mop and bucket",
    "candles and holders for home decor",
]

_VAGUE_QUERIES: list[str] = [
    "good quality product under budget",
    "best rated item for gifting",
    "something useful for daily life",
    "popular product with good reviews",
    "lightweight and compact",
    "durable and long lasting",
    "eco friendly and sustainable",
    "easy to use for beginners",
    "comfortable and stylish",
    "value for money purchase",
]

_ALL_QUERIES: list[str] = (
    _BOOKS_QUERIES + _CLOTHING_QUERIES +
    _ELECTRONICS_QUERIES + _HOUSEHOLD_QUERIES +
    _VAGUE_QUERIES
)

# ---------------------------------------------------------------------------
# Atlas override payload — pinned to retail collection on every request
# ---------------------------------------------------------------------------

def _atlas_overrides() -> dict[str, Any]:
    return {
        "collection":    _COLLECTION,
        "vector_index":  _VECTOR_INDEX,
        "search_index":  _SEARCH_INDEX,
        "text_key":      _TEXT_KEY,
        "embedding_key": None,          # AutoEmbed — no client-side embedding field
    }


def _base_payload(query: str, top_k: int = _TOP_K) -> dict[str, Any]:
    return {
        "query":      query,
        "top_k":      top_k,
        "summarize":  False,            # disabled by default — reduces LLM cost & latency
        "understand": False,
        "atlas":      _atlas_overrides(),
    }


def _payload_with_filter(query: str, category: str, top_k: int = _TOP_K) -> dict[str, Any]:
    """Payload with a category pre-filter (uses Atlas $vectorSearch filter field)."""
    payload = _base_payload(query, top_k)
    payload["filters"] = {"Category": category}
    return payload

# ---------------------------------------------------------------------------
# Shared POST helper
# ---------------------------------------------------------------------------

def _post(client, endpoint: str, payload: dict[str, Any], name: str) -> None:
    with client.post(
        endpoint,
        json=payload,
        name=name,
        catch_response=True,
    ) as resp:
        if resp.status_code == 503:
            resp.failure(f"HTTP 503 — server at capacity")
            raise RescheduleTask()
        elif resp.status_code == 429:
            resp.failure(f"HTTP 429 — rate limited")
            raise RescheduleTask()
        elif resp.status_code >= 500:
            resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
        elif resp.status_code >= 400:
            resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            try:
                data = resp.json()
                results = data.get("results") or []
                resp.success()
                if not results:
                    # Atlas returned 0 results — not a hard failure but worth
                    # logging (cold HNSW cache or index building).
                    pass
            except Exception:
                resp.failure("Response is not valid JSON")


# ===========================================================================
# User class 1 — RetailHybridUser
# Primary workload: RRF hybrid (vector + fulltext) on retail products.
# Weight 3 — spawned 3× more than other classes.
# ===========================================================================

class RetailHybridUser(HttpUser):
    """Simulates product-search traffic via hybrid retrieval."""

    weight    = 3
    wait_time = between(_THINK_MIN, _THINK_MAX)

    @task(4)
    def hybrid_books(self) -> None:
        _post(self.client, "/retrieve/hybrid",
              _base_payload(random.choice(_BOOKS_QUERIES)),
              name="/retrieve/hybrid [books]")

    @task(4)
    def hybrid_electronics(self) -> None:
        _post(self.client, "/retrieve/hybrid",
              _base_payload(random.choice(_ELECTRONICS_QUERIES)),
              name="/retrieve/hybrid [electronics]")

    @task(4)
    def hybrid_household(self) -> None:
        _post(self.client, "/retrieve/hybrid",
              _base_payload(random.choice(_HOUSEHOLD_QUERIES)),
              name="/retrieve/hybrid [household]")

    @task(4)
    def hybrid_clothing(self) -> None:
        _post(self.client, "/retrieve/hybrid",
              _base_payload(random.choice(_CLOTHING_QUERIES)),
              name="/retrieve/hybrid [clothing]")

    @task(2)
    def hybrid_with_category_filter(self) -> None:
        """Hybrid search narrowed to a single category via Atlas filter."""
        category = random.choice(["Books", "Electronics", "Household", "Clothing & Accessories"])
        query_bank = {
            "Books":                   _BOOKS_QUERIES,
            "Electronics":             _ELECTRONICS_QUERIES,
            "Household":               _HOUSEHOLD_QUERIES,
            "Clothing & Accessories":  _CLOTHING_QUERIES,
        }
        query = random.choice(query_bank[category])
        _post(self.client, "/retrieve/hybrid",
              _payload_with_filter(query, category),
              name="/retrieve/hybrid [filtered]")

    @task(2)
    def hybrid_top_k_variation(self) -> None:
        """Vary top_k (3, 5, 8, 10) to stress result-set serialisation."""
        _post(self.client, "/retrieve/hybrid",
              _base_payload(random.choice(_ALL_QUERIES),
                            top_k=random.choice([3, 5, 8, 10])),
              name="/retrieve/hybrid [top_k var]")

    @task(1)
    def health_check(self) -> None:
        with self.client.get("/health", name="/health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Health check failed: {resp.status_code}")
            else:
                resp.success()


# ===========================================================================
# User class 2 — RetailVectorUser
# Pure semantic (autoEmbed voyage-4) queries — stresses the HNSW index path.
# Weight 2.
# ===========================================================================

class RetailVectorUser(HttpUser):
    """Simulates semantic / discovery queries via pure vector search."""

    weight    = 2
    wait_time = between(_THINK_MIN, _THINK_MAX)

    @task(4)
    def vector_books(self) -> None:
        _post(self.client, "/retrieve/vector",
              _base_payload(random.choice(_BOOKS_QUERIES)),
              name="/retrieve/vector [books]")

    @task(4)
    def vector_electronics(self) -> None:
        _post(self.client, "/retrieve/vector",
              _base_payload(random.choice(_ELECTRONICS_QUERIES)),
              name="/retrieve/vector [electronics]")

    @task(4)
    def vector_household(self) -> None:
        _post(self.client, "/retrieve/vector",
              _base_payload(random.choice(_HOUSEHOLD_QUERIES)),
              name="/retrieve/vector [household]")

    @task(4)
    def vector_clothing(self) -> None:
        _post(self.client, "/retrieve/vector",
              _base_payload(random.choice(_CLOTHING_QUERIES)),
              name="/retrieve/vector [clothing]")

    @task(2)
    def vector_vague(self) -> None:
        """Short/ambiguous queries that rely purely on semantic similarity."""
        _post(self.client, "/retrieve/vector",
              _base_payload(random.choice(_VAGUE_QUERIES)),
              name="/retrieve/vector [vague]")


# ===========================================================================
# User class 3 — RetailFulltextUser
# Keyword / exact-match queries via Atlas Search (Lucene).
# Weight 1 — Lucene is fastest; kept lower to reflect real traffic split.
# ===========================================================================

class RetailFulltextUser(HttpUser):
    """Simulates keyword search and settings reads."""

    weight    = 1
    wait_time = between(_THINK_MIN, _THINK_MAX)

    @task(4)
    def fulltext_electronics(self) -> None:
        _post(self.client, "/retrieve/fulltext",
              _base_payload(random.choice(_ELECTRONICS_QUERIES)),
              name="/retrieve/fulltext [electronics]")

    @task(4)
    def fulltext_books(self) -> None:
        _post(self.client, "/retrieve/fulltext",
              _base_payload(random.choice(_BOOKS_QUERIES)),
              name="/retrieve/fulltext [books]")

    @task(4)
    def fulltext_household(self) -> None:
        _post(self.client, "/retrieve/fulltext",
              _base_payload(random.choice(_HOUSEHOLD_QUERIES)),
              name="/retrieve/fulltext [household]")

    @task(4)
    def fulltext_clothing(self) -> None:
        _post(self.client, "/retrieve/fulltext",
              _base_payload(random.choice(_CLOTHING_QUERIES)),
              name="/retrieve/fulltext [clothing]")

    @task(1)
    def read_settings(self) -> None:
        with self.client.get("/settings", name="/settings", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"GET /settings failed: {resp.status_code}")
            else:
                resp.success()


# ===========================================================================
# User class 4 — RetailAutoUser
# Full NLU + planner pipeline — most expensive per request.
# Weight 1, longer think time.
# ===========================================================================

class RetailAutoUser(HttpUser):
    """Exercises the /retrieve auto endpoint: QU layer → planner → retrieval."""

    weight    = 1
    wait_time = between(_THINK_MIN * 2, _THINK_MAX * 2)  # extra think time — slow path

    @task(3)
    def auto_books(self) -> None:
        _post(self.client, "/retrieve",
              _base_payload(random.choice(_BOOKS_QUERIES)),
              name="/retrieve [auto, books]")

    @task(3)
    def auto_electronics(self) -> None:
        _post(self.client, "/retrieve",
              _base_payload(random.choice(_ELECTRONICS_QUERIES)),
              name="/retrieve [auto, electronics]")

    @task(2)
    def auto_household(self) -> None:
        _post(self.client, "/retrieve",
              _base_payload(random.choice(_HOUSEHOLD_QUERIES)),
              name="/retrieve [auto, household]")

    @task(2)
    def auto_clothing(self) -> None:
        _post(self.client, "/retrieve",
              _base_payload(random.choice(_CLOTHING_QUERIES)),
              name="/retrieve [auto, clothing]")

    @task(1)
    def auto_vague(self) -> None:
        _post(self.client, "/retrieve",
              _base_payload(random.choice(_VAGUE_QUERIES)),
              name="/retrieve [auto, vague]")


# ===========================================================================
# Report output directory
# ===========================================================================

_TESTS_DIR = Path(__file__).parent


# ===========================================================================
# Machine / environment info
# ===========================================================================

def _collect_machine_info() -> dict[str, str]:
    info: dict[str, str] = {}
    info["os"]      = platform.system()
    info["os_ver"]  = platform.version().split()[0] if platform.version() else "unknown"
    info["machine"] = platform.machine()
    info["cpu_logical"] = str(multiprocessing.cpu_count() or "?")
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
    info["python"] = platform.python_version()
    info["locust_workers"] = os.environ.get("LOCUST_WORKERS", "1 (master)")
    info["target_cpu"]       = os.environ.get("CLOUD_RUN_CPU",      "unknown")
    info["target_mem"]       = os.environ.get("CLOUD_RUN_MEM",      "unknown")
    info["target_instances"] = os.environ.get("CLOUD_RUN_MAX_INST", "unknown")
    info["target_concurr"]   = os.environ.get("CLOUD_RUN_CONCURR",  "unknown")
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
    lines: list[str] = []
    total_reqs  = total.num_requests
    total_fails = total.num_failures
    fail_pct    = 100.0 * total_fails / max(total_reqs, 1)
    obs_tps     = total.total_rps
    p50_ms      = total.get_response_time_percentile(0.50) or 0
    avg_think_s = (think_min + think_max) / 2.0
    avg_resp_s  = (p50_ms / 1000.0) if p50_ms else 0.001
    littles_tps = target_user_count / (avg_think_s + avg_resp_s) if avg_resp_s else 0

    strategy_tps: list[tuple[str, float, int, int]] = []
    for e in sorted(entries, key=lambda x: -x.total_rps):
        if e.num_requests == 0:
            continue
        ep_fail_pct = int(100.0 * e.num_failures / e.num_requests)
        ep_p50      = int(e.get_response_time_percentile(0.50) or 0)
        strategy_tps.append((f"{e.method} {e.name}", e.total_rps, ep_p50, ep_fail_pct))

    if fail_pct == 0:
        safe_users = int(target_user_count * 1.2)
        capacity_verdict = (
            f"✅ Server handled **{target_user_count:,} users** with zero failures. "
            f"Observed TPS: **{obs_tps:.1f}**.\n\n"
            f"  **Estimated safe ceiling**: ~{safe_users:,} users (+20% headroom)."
        )
    elif fail_pct < 5:
        safe_tps = obs_tps * (1 - fail_pct / 100)
        capacity_verdict = (
            f"⚠️ Minor failures ({fail_pct:.1f}%). "
            f"Effective successful TPS: **{safe_tps:.1f}**. "
            f"Reduce concurrency ~{int(fail_pct + 10)}% for a stable baseline."
        )
    else:
        baseline_resp_s = 0.44
        safe_tps        = 1.0 / (avg_think_s + baseline_resp_s)
        safe_users_est  = int(safe_tps * (avg_think_s + baseline_resp_s))
        capacity_verdict = (
            f"❌ High failure rate ({fail_pct:.1f}%) — server over capacity at "
            f"{target_user_count:,} users.\n\n"
            f"  **Estimated safe ceiling** (Little's Law, p50≈440 ms): "
            f"**~{safe_users_est:,} users / {safe_tps:.0f} TPS**.\n\n"
            f"  **Observed TPS (incl. failures):** {obs_tps:.1f}"
        )

    lines.append(capacity_verdict)
    lines.append("")
    lines.append("### TPS by endpoint")
    lines.append("")
    lines.append("| Endpoint | TPS | p50 (ms) | Fail % |")
    lines.append("|---|---|---|---|")
    for name, tps, p50, fp in strategy_tps:
        flag = "❌" if fp > 5 else ("⚠️" if fp > 0 else "✅")
        lines.append(f"| `{name}` | {tps:.2f} | {p50} | {flag} {fp}% |")
    lines.append("")
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
# Report builder
# ===========================================================================

_SLA: dict[str, dict[str, int]] = {
    "fulltext": {"p50": 500,  "p95": 800,   "p99": 1_000},
    "vector":   {"p50": 800,  "p95": 1_500, "p99": 3_000},
    "hybrid":   {"p50": 800,  "p95": 1_500, "p99": 3_000},
    "auto":     {"p50": 2_000,"p95": 3_000, "p99": 5_000},
    "infra":    {"p50": 50,   "p95": 200,   "p99": 500},
}


def _endpoint_group(name: str) -> str:
    n = name.lower()
    if "auto"     in n: return "auto"
    if "hybrid"   in n: return "hybrid"
    if "vector"   in n: return "vector"
    if "fulltext" in n: return "fulltext"
    return "infra"


def _pct(entry, p: float) -> int:
    v = entry.get_response_time_percentile(p)
    return int(v) if v is not None else 0


def _sla_icon(actual: int, limit: int) -> str:
    return "✅" if actual <= limit else "❌"


def _diagnose(entries: list) -> list[str]:
    findings: list[str] = []
    total_reqs  = sum(e.num_requests for e in entries)
    total_fails = sum(e.num_failures for e in entries)
    fail_pct    = 100.0 * total_fails / max(total_reqs, 1)

    if fail_pct == 0:
        findings.append("✅ **Zero failures** — server is stable at this concurrency.")
    elif fail_pct < 1:
        findings.append(f"⚠️ Low failure rate ({fail_pct:.2f}%) — investigate individual errors.")
    else:
        findings.append(f"❌ High failure rate ({fail_pct:.2f}%) — reduce concurrency or fix errors.")

    for e in [x for x in entries if x.method == "POST"]:
        p50    = _pct(e, 0.50)
        p99    = _pct(e, 0.99)
        mx     = int(e.max_response_time)
        spread = p99 / p50 if p50 > 0 else 0
        grp    = _endpoint_group(e.name)
        if spread >= 10:
            if grp in ("auto",):
                cause = (
                    "LLM call timeout (QueryUnderstanding + RetrievalPlanner). "
                    "Fix: set `planner.llm_timeout_s` in searchaas.yaml (default 5 s)."
                )
            else:
                cause = (
                    "Atlas HNSW cold-cache miss or connection pool exhaustion. "
                    "Fix: ensure `retrieval.max_time_ms` is set and raise `ATLAS_MAX_POOL`."
                )
            findings.append(
                f"❌ **High tail latency** on `{e.name}`: p99 ({p99} ms) is "
                f"{spread:.0f}× the p50 ({p50} ms). Likely cause: {cause}"
            )
        elif spread >= 5:
            findings.append(
                f"⚠️ **Elevated tail** on `{e.name}`: p99 ({p99} ms) is "
                f"{spread:.0f}× p50 ({p50} ms). Monitor under higher load."
            )
        if mx > 4_000:
            findings.append(
                f"⚠️ **Max spike** on `{e.name}`: {mx} ms — consider a 4 s client-side timeout."
            )

    ft  = [e for e in entries if "fulltext" in e.name.lower()]
    hyb = [e for e in entries if "hybrid"   in e.name.lower()]
    if ft and hyb:
        ft_p50  = int(sum(_pct(e, 0.50) for e in ft)  / len(ft))
        hyb_p50 = int(sum(_pct(e, 0.50) for e in hyb) / len(hyb))
        findings.append(
            f"ℹ️ **Hybrid overhead over fulltext**: +{hyb_p50 - ft_p50} ms at p50 "
            f"({hyb_p50} ms vs {ft_p50} ms). RRF fusion cost — acceptable."
        )

    auto = [e for e in entries if "auto" in e.name.lower()]
    if auto and hyb:
        auto_avg = int(sum(e.avg_response_time for e in auto) / len(auto))
        hyb_avg  = int(sum(e.avg_response_time for e in hyb)  / len(hyb))
        findings.append(
            f"ℹ️ **Auto-route NLU overhead**: +{auto_avg - hyb_avg} ms avg "
            f"({auto_avg} ms vs {hyb_avg} ms for explicit hybrid). "
            f"Two LLM calls (QueryUnderstanding + RetrievalPlanner)."
        )

    return findings


def _build_report(environment, run_time_s: float = 0.0) -> str:
    stats   = environment.stats
    total   = stats.total
    entries = sorted(
        [e for e in stats.entries.values() if e.num_requests > 0],
        key=lambda e: e.name,
    )

    now               = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    target_user_count = getattr(environment.runner, "target_user_count", 0) or 0
    host              = environment.host or "unknown"
    total_reqs        = total.num_requests
    total_fails       = total.num_failures
    fail_pct          = 100.0 * total_fails / max(total_reqs, 1)
    tps               = total.total_rps
    p50_all           = _pct(total, 0.50)
    p95_all           = _pct(total, 0.95)
    p99_all           = _pct(total, 0.99)
    max_all           = int(total.max_response_time)
    run_dur_str       = f"{run_time_s:.0f} s" if run_time_s else "unknown"
    findings          = _diagnose(entries)
    capacity_md       = _capacity_analysis(
        total, entries, run_time_s, target_user_count, _THINK_MIN, _THINK_MAX,
    )

    m = _MACHINE
    target_line = ""
    if m["target_cpu"] != "unknown":
        target_line = (
            f"\n\n**Target server:** {m['target_cpu']} vCPU · {m['target_mem']} RAM · "
            f"max {m['target_instances']} instances · {m['target_concurr']} concurrency/instance"
        )

    machine_md = textwrap.dedent(f"""\
    | Property | Value |
    |---|---|
    | OS | {m['os']} {m['os_ver']} ({m['machine']}) |
    | CPU (logical / physical) | {m['cpu_logical']} / {m['cpu_physical']} cores |
    | CPU freq (max) | {m['cpu_freq_ghz']} GHz |
    | RAM total / available | {m['ram_total_gb']} GB / {m['ram_avail_gb']} GB |
    | Python | {m['python']} |
    | Locust workers | {m['locust_workers']} |
    """) + target_line

    def _row(e) -> str:
        fp   = 100.0 * e.num_failures / max(e.num_requests, 1)
        fail = f"**{e.num_failures}** ({fp:.1f}%)" if e.num_failures else "0"
        return (
            f"| `{e.method} {e.name}` "
            f"| {e.num_requests:,} | {fail} "
            f"| {int(e.avg_response_time)} | {_pct(e,0.50)} | {_pct(e,0.90)} "
            f"| {_pct(e,0.95)} | {_pct(e,0.99)} | {int(e.max_response_time)} "
            f"| {e.total_rps:.2f} |"
        )

    def _spread_row(e) -> str:
        p50 = _pct(e, 0.50)
        p99 = _pct(e, 0.99)
        spread = f"{p99/p50:.1f}×" if p50 > 0 else "n/a"
        flag = "🔴" if (p50 > 0 and p99/p50 >= 10) else ("🟡" if (p50 > 0 and p99/p50 >= 5) else "🟢")
        return f"| `{e.name}` | {p50} | {p99} | {int(e.max_response_time)} | {spread} | {flag} |"

    def _sla_row(e) -> str:
        sla = _SLA[_endpoint_group(e.name)]
        p50, p95, p99 = _pct(e,0.50), _pct(e,0.95), _pct(e,0.99)
        return (
            f"| `{e.method} {e.name}` "
            f"| {_sla_icon(p50,sla['p50'])} {p50} ms (≤{sla['p50']}) "
            f"| {_sla_icon(p95,sla['p95'])} {p95} ms (≤{sla['p95']}) "
            f"| {_sla_icon(p99,sla['p99'])} {p99} ms (≤{sla['p99']}) |"
        )

    table_rows  = "\n".join(_row(e)        for e in entries)
    spread_rows = "\n".join(_spread_row(e) for e in entries if e.method == "POST")
    sla_rows    = "\n".join(_sla_row(e)    for e in entries if e.method in ("GET","POST"))
    findings_md = "\n".join(f"- {f}" for f in findings)

    if fail_pct == 0 and p99_all <= 4_000:
        verdict = "🟢 **PASS** — Stable. Zero failures. Tail latency within acceptable range."
    elif fail_pct == 0:
        verdict = "🟡 **PASS WITH WARNINGS** — No failures but p99 tail is high."
    elif fail_pct < 1:
        verdict = "🟡 **MARGINAL** — Low failure rate. Investigate before scaling."
    else:
        verdict = "🔴 **FAIL** — Failure rate exceeds threshold. Needs tuning."

    artefacts = []
    for name in ("load_report.html","load_report_stats.csv",
                 "load_report_stats_history.csv","load_report_failures.csv"):
        p = _TESTS_DIR / name
        if p.exists():
            artefacts.append(f"| `{name}` | {p} |")
    artefacts_md = (
        "| File | Path |\n|---|---|\n" + "\n".join(artefacts)
        if artefacts else "_Run with `--html` and `--csv` to generate artefact files._"
    )

    return textwrap.dedent(f"""\
    # SearchaaS Load Test Report — kaggle_dataset.retail

    **Generated:** {now}
    **Host:** {host}
    **Collection:** `{_COLLECTION}` (50,425 docs) · vector index: `{_VECTOR_INDEX}` · text key: `{_TEXT_KEY}`
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

    | Endpoint | p50 (ms) | p99 (ms) | Max (ms) | Spread | Status |
    |---|---|---|---|---|---|
    {spread_rows}

    🟢 < 5× &nbsp; 🟡 5–10× &nbsp; 🔴 ≥ 10×

    ---

    ## 7. SLA Assessment

    | Endpoint | p50 | p95 | p99 |
    |---|---|---|---|
    {sla_rows}

    **SLA thresholds:**

    | Group | p50 | p95 | p99 |
    |---|---|---|---|
    | Infrastructure | 50 ms | 200 ms | 500 ms |
    | Fulltext | 500 ms | 800 ms | 1,000 ms |
    | Vector | 800 ms | 1,500 ms | 3,000 ms |
    | Hybrid | 800 ms | 1,500 ms | 3,000 ms |
    | Auto-route | 2,000 ms | 3,000 ms | 5,000 ms |

    ---

    ## 8. Findings & Recommendations

    {findings_md}

    ---

    ## 9. Report Artefacts

    {artefacts_md}
    """)


# ===========================================================================
# Event hooks
# ===========================================================================

_test_start_time: float = 0.0


@events.test_start.add_listener
def on_test_start(environment, **kwargs) -> None:
    import time as _t
    global _test_start_time
    _test_start_time = _t.monotonic()
    print("\n" + "=" * 62)
    print("  SearchaaS Load Test — kaggle_dataset.retail")
    print("=" * 62)
    print(f"  host        : {environment.host}")
    print(f"  collection  : {_COLLECTION}  (50,425 docs)")
    print(f"  vector_idx  : {_VECTOR_INDEX}  (voyage-4 autoEmbed, path='{_TEXT_KEY}')")
    print(f"  search_idx  : {_SEARCH_INDEX}  (Lucene, dynamic mapping)")
    print(f"  top_k       : {_TOP_K}")
    print(f"  think time  : {_THINK_MIN}s – {_THINK_MAX}s")
    print(f"  machine     : {_MACHINE['os']} · {_MACHINE['cpu_logical']} vCPU · {_MACHINE['ram_total_gb']} GB RAM")
    print("=" * 62 + "\n")


@events.quitting.add_listener
def on_quitting(environment, **kwargs) -> None:
    import time as _t
    run_time_s = _t.monotonic() - _test_start_time if _test_start_time else 0

    stats = environment.stats
    total = stats.total
    if total.num_requests == 0:
        print("[report] No requests recorded — skipping report generation.")
        return

    fail_pct = 100 * total.num_failures / max(total.num_requests, 1)

    print("\n" + "=" * 62)
    print("  SearchaaS Load Test — summary")
    print("=" * 62)
    print(f"  requests   : {total.num_requests:,}")
    print(f"  failures   : {total.num_failures:,}  ({fail_pct:.1f}%)")
    print(f"  TPS        : {total.total_rps:.1f}")
    print(f"  p50 ms     : {total.get_response_time_percentile(0.50):.0f}")
    print(f"  p95 ms     : {total.get_response_time_percentile(0.95):.0f}")
    print(f"  p99 ms     : {total.get_response_time_percentile(0.99):.0f}")
    print(f"  max ms     : {total.max_response_time:.0f}")
    print(f"  duration   : {run_time_s:.0f}s")
    print("=" * 62 + "\n")

    report_path = _TESTS_DIR / "LOAD_TEST_REPORT.md"
    md = _build_report(environment, run_time_s)
    report_path.write_text(md, encoding="utf-8")
    print(f"[report] Written → {report_path}")
