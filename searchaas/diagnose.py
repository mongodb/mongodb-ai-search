"""
SearchaaS diagnostics CLI.

Walks the full Phase-1 stack and reports exactly where vector search is
failing. Run with:

    python -m searchaas.diagnose --query "your test query" --k 5

Stages checked (in order):

    1. Config load + .env resolution
    2. Atlas connectivity (ping)
    3. Collection stats: document count, sample embedding dimension,
       Atlas Search / Vector Search index inventory
    4. Embedding provider construction + a live `embed_query` probe
    5. Dimension match between query vector and stored vectors
    6. `$vectorSearch` execution via MongoDBAtlasVectorSearch.as_retriever

Each stage prints a clear PASS/FAIL line; on FAIL the process exits with a
non-zero code so this is CI-friendly.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from searchaas.observability import configure_logging, get_logger

configure_logging()
log = get_logger("searchaas.diagnose")


_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RST = "\033[0m"


def _ok(msg: str) -> None:    print(f"{_GREEN}[PASS]{_RST} {msg}")
def _fail(msg: str) -> None:  print(f"{_RED}[FAIL]{_RST} {msg}")
def _warn(msg: str) -> None:  print(f"{_YELLOW}[WARN]{_RST} {msg}")
def _info(msg: str) -> None:  print(f"       {_DIM}{msg}{_RST}")


def _dump(label: str, payload: Any) -> None:
    print(f"       {_DIM}{label}: {json.dumps(payload, default=str, indent=2)}{_RST}")


def main() -> int:
    ap = argparse.ArgumentParser(description="SearchaaS vector-search diagnostics")
    ap.add_argument("--query", default="diagnostic ping", help="probe query")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--filters", default="{}", help="JSON filter object")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    try:
        filters = json.loads(args.filters or "{}")
    except json.JSONDecodeError as exc:
        print(f"invalid --filters JSON: {exc}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"query": args.query, "k": args.k, "filters": filters, "stages": {}}

    # ----- 1. config -------------------------------------------------------
    print("\n=== 1/6  config ===")
    try:
        from searchaas.config import load_config
        cfg = load_config()
        _ok("config loaded")
        _info(f"embeddings={cfg.embeddings.provider} llm={cfg.planner.llm_provider} "
              f"atlas={cfg.atlas.database}.{cfg.atlas.collection} "
              f"vector_index={cfg.atlas.vector_index} search_index={cfg.atlas.search_index} "
              f"text_key={cfg.atlas.text_key!r} embedding_key={cfg.atlas.embedding_key!r} "
              f"dimensions={cfg.atlas.dimensions}")
        report["stages"]["config"] = {"ok": True, "embeddings": cfg.embeddings.provider,
                                       "llm": cfg.planner.llm_provider}
    except Exception as exc:
        _fail(f"config load failed — {exc}")
        report["stages"]["config"] = {"ok": False, "error": str(exc)}
        return _finish(report, args.json, exit_code=1)

    # ----- 2. atlas ping ---------------------------------------------------
    print("\n=== 2/6  atlas connectivity ===")
    from searchaas.infrastructure import AtlasFactory
    ping = AtlasFactory.ping()
    report["stages"]["atlas_ping"] = ping
    if ping.get("ok"):
        _ok(f"Atlas reachable ({ping['latency_ms']} ms)  {ping['uri']}")
    else:
        _fail(f"Atlas unreachable — kind={ping.get('error_kind')}")
        _info(ping.get("error", ""))
        _info("Check: ATLAS_URI correctness, Atlas IP allowlist, DNS, "
              "user permissions, cluster paused?")
        return _finish(report, args.json, exit_code=1)

    # ----- 3. collection stats --------------------------------------------
    print("\n=== 3/6  collection inventory ===")
    stats = AtlasFactory.collection_stats()
    report["stages"]["collection"] = stats
    doc_count = stats.get("doc_count")
    stored_dim = stats.get("embedding_dimensions")
    indexes = stats.get("search_indexes") or []
    _info(f"namespace: {stats['database']}.{stats['collection']}  docs≈{doc_count}")
    if doc_count == 0 or doc_count is None:
        _warn("Collection has zero documents — vector search will return [].")
    if stored_dim is None:
        if cfg.embeddings.provider == "auto":
            _info("No client-side embedding field on sample docs — expected in "
                  "AutoEmbeddings mode (Atlas stores vectors internally).")
        else:
            _warn("No sample doc with an `embedding` field — check ingestion or `embedding_key`.")
    else:
        _info(f"stored embedding dimensions: {stored_dim} "
              f"(model={stats.get('sample_embedding_model')!r})")

    by_name = {i.get("name"): i for i in indexes}
    if indexes:
        _info(f"search indexes present: {list(by_name.keys())}")
    else:
        _warn("`list_search_indexes` returned nothing — older driver / non-Atlas cluster?")

    # ---- vector index validation ----
    is_auto = cfg.embeddings.provider == "auto"
    # In AutoEmbeddings mode the index's vector path is the text field itself
    # (Atlas embeds it server-side); embedding_key must be null.
    expected_path = cfg.atlas.text_key if is_auto else cfg.atlas.embedding_key
    vidx = by_name.get(cfg.atlas.vector_index)
    if not vidx:
        _fail(f"vector index `{cfg.atlas.vector_index}` NOT found on this collection")
        if is_auto:
            _info(f"Create it with type='vectorSearch' and a field "
                  f"of type='autoEmbed', path='{cfg.atlas.text_key}', "
                  f"model='{(cfg.embeddings.config or {}).get('model', '<model>')}'")
        else:
            _info(f"Create it with type='vectorSearch', path='{cfg.atlas.embedding_key}', "
                  f"numDimensions=<embedder_dim>, similarity='{cfg.atlas.relevance_score_fn}'.")
    else:
        if vidx.get("type") != "vectorSearch":
            _fail(f"index `{cfg.atlas.vector_index}` exists but type={vidx.get('type')!r} "
                  "(must be 'vectorSearch')")
        else:
            fields = (vidx.get("latestDefinition") or {}).get("fields") or []
            expected_types = ("autoEmbed",) if is_auto else ("vector", "autoEmbed")
            vec_fields = [f for f in fields if f.get("type") in expected_types]
            paths = [f.get("path") for f in vec_fields]
            if expected_path not in paths:
                if is_auto:
                    _fail(
                        f"AutoEmbeddings: index `{cfg.atlas.vector_index}` has no "
                        f"`type=autoEmbed` field at path={cfg.atlas.text_key!r}. "
                        f"Found vector path(s)={paths}."
                    )
                    _info(
                        "Recreate the index with: "
                        f"type='autoEmbed', path='{cfg.atlas.text_key}', "
                        f"model='{(cfg.embeddings.config or {}).get('model', '<model>')}'"
                    )
                else:
                    _fail(
                        f"index `{cfg.atlas.vector_index}` indexes path(s) {paths} "
                        f"but config embedding_key={cfg.atlas.embedding_key!r}"
                    )
                    _info(
                        "This is exactly what produces "
                        "'<field> is not indexed as vector'. "
                        "Either change atlas.embedding_key in YAML to match the index, "
                        "OR recreate the index with the desired path."
                    )
            else:
                match = next(f for f in vec_fields if f.get("path") == expected_path)
                idx_dim = match.get("numDimensions")
                if is_auto:
                    _ok(f"vector index `{cfg.atlas.vector_index}` indexes "
                        f"path={expected_path!r} type=autoEmbed "
                        f"model={match.get('model')!r}")
                else:
                    _ok(f"vector index `{cfg.atlas.vector_index}` indexes "
                        f"path={expected_path!r} dim={idx_dim} "
                        f"sim={match.get('similarity')}")
                # stash for stage 5
                report["stages"]["collection"]["index_dimensions"] = idx_dim
                # Dimension drift check is meaningless in auto mode (index has no
                # numDimensions and config must be -1).
                cfg_dim = cfg.atlas.dimensions
                if not is_auto and cfg_dim and cfg_dim != -1 and idx_dim and cfg_dim != idx_dim:
                    _fail(f"atlas.dimensions={cfg_dim} but index numDimensions={idx_dim}")
                    _info("Update YAML atlas.dimensions to match the index, OR rebuild the index.")
                elif not is_auto and cfg_dim and cfg_dim != -1:
                    _ok(f"atlas.dimensions={cfg_dim} matches index numDimensions={idx_dim}")

    if cfg.atlas.search_index in by_name:
        _ok(f"search index `{cfg.atlas.search_index}` exists (used by fulltext/hybrid)")
    else:
        _warn(f"search index `{cfg.atlas.search_index}` not found — hybrid/fulltext will fail")

    # ----- 4. embedder probe ----------------------------------------------
    print("\n=== 4/6  embedder probe ===")
    from searchaas.embeddings import EmbeddingFactory
    embedder = EmbeddingFactory.create(cfg.embeddings.provider, cfg.embeddings.config)
    probe = EmbeddingFactory.probe(embedder, sample=args.query)
    report["stages"]["embedder"] = probe
    if probe.get("ok"):
        _ok(f"embed_query OK  dim={probe['dimensions']}  latency={probe['latency_ms']} ms")
    else:
        _fail(f"embed_query failed — kind={probe.get('error_kind')}")
        _info(probe.get("error", ""))
        _info("Check: API key env var set? Model name correct? Network egress allowed?")
        return _finish(report, args.json, exit_code=1)

    # ----- 5. dimension match ---------------------------------------------
    print("\n=== 5/6  dimension match ===")
    q_dim = probe.get("dimensions")
    idx_dim = (report["stages"].get("collection") or {}).get("index_dimensions")
    dims = {"query": q_dim, "stored": stored_dim, "index": idx_dim,
            "mode": "auto" if cfg.embeddings.provider == "auto" else "client"}
    report["stages"]["dimensions"] = dims

    # In AutoEmbeddings mode, query and stored dimensions are unobservable from
    # the client side (Atlas owns them), so dimension matching is a no-op.
    if cfg.embeddings.provider == "auto":
        _ok("AutoEmbeddings: dimensions are managed by Atlas, no client-side check needed")
        dims["ok"] = True
        report["stages"]["dimensions"] = dims
        # skip to next stage
        mismatches: list[str] = []
    else:
        mismatches = []
        if q_dim and idx_dim and q_dim != idx_dim:
            mismatches.append(f"query({q_dim}) != index({idx_dim})")
        if q_dim and stored_dim and q_dim != stored_dim:
            mismatches.append(f"query({q_dim}) != stored({stored_dim})")
        if stored_dim and idx_dim and stored_dim != idx_dim:
            mismatches.append(f"stored({stored_dim}) != index({idx_dim})")

    if mismatches:
        _fail("DIMENSION MISMATCH: " + ", ".join(mismatches))
        _info("Options:")
        _info("  - change embeddings.provider/model so the query dim matches the index")
        _info(f"  - for voyage-4: add `output_dimension: {idx_dim or '<index_dim>'}` under embeddings.config")
        _info("  - recreate the Atlas Vector Search index with matching numDimensions")
        dims["ok"] = False
        report["stages"]["dimensions"] = dims
        return _finish(report, args.json, exit_code=1)
    if q_dim and (idx_dim or stored_dim):
        _ok(f"dimensions match {dims}")
        dims["ok"] = True
    else:
        _warn(f"Could not fully verify dimensions {dims}")
        dims["ok"] = None

    # ----- 6. instrumented $vectorSearch ----------------------------------
    print("\n=== 6/6  $vectorSearch ===")
    from searchaas.app.bootstrap import get_container
    container = get_container()
    vector_report = container.retrievers.run_vector(args.query, k=args.k, filters=filters)
    report["stages"]["vector_search"] = vector_report
    if vector_report.get("ok"):
        _ok(f"{vector_report['result_count']} hit(s) in {vector_report.get('search_ms')} ms")
        for i, r in enumerate(vector_report.get("results", []), 1):
            _info(f"  #{i}  {r['content'][:140]!r}")
    else:
        _fail(f"failed at stage `{vector_report.get('stage')}` — {vector_report.get('error_kind')}")
        _info(vector_report.get("error", ""))
        if "hint" in vector_report:
            _info(f"hint: {vector_report['hint']}")
        return _finish(report, args.json, exit_code=1)

    return _finish(report, args.json, exit_code=0)


def _finish(report: dict, as_json: bool, exit_code: int) -> int:
    if as_json:
        print("\n" + json.dumps(report, default=str, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
