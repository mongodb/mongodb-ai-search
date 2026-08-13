#!/usr/bin/env python3
# =============================================================================
# SearchaaS — Vertex AI Agent Engine (Reasoning Engine) deployer
#
# Wraps the SearchaaS retrieval stack (searchaas/) in a Reasoning-Engine-
# compatible agent class and deploys it to the Vertex AI Agent Engine managed
# runtime (the Gemini agent platform's managed agent service).
#
# NO Cloud Run, NO Dockerfile: the Agent Engine service builds and hosts the
# runtime container server-side from:
#   * the pickled SearchaaSAgent instance  (cloudpickle, uploaded to staging)
#   * extra_packages=["searchaas"]          (repo package, tarred + uploaded)
#   * requirements                          (pip deps, from requirements.txt)
#   * env_vars / SecretRef env vars         (config + Secret Manager secrets)
#
# The SearchaaSAgent class is defined in __main__ on purpose: cloudpickle
# serializes __main__ classes BY VALUE, so this file does not need to be
# importable inside the remote runtime. All `searchaas.*` imports are deferred
# into set_up()/_execute() so they resolve remotely against extra_packages.
#
# Usage (normally invoked by deploy.sh):
#   python deploy_agent_engine.py \
#       --project my-gcp-project --region us-central1 \
#       --staging-bucket gs://my-gcp-project-agent-engine-staging \
#       [--display-name searchaas-agent] [--engine-id <id-to-update>]
# =============================================================================
from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


# ─────────────────────────────────────────────────────────────────────────────
# Per-request override models — mirror searchaas.api.app.AtlasOverrides /
# RetrievalOverrides. Defined locally (not imported from searchaas.api.app) so
# pickling stays dependency-light; pydantic is guaranteed in the remote runtime
# via requirements.txt. RetrieverFactory only needs attribute access +
# .model_dump(exclude_none=True), so any pydantic model with these fields works.
# ─────────────────────────────────────────────────────────────────────────────
from pydantic import BaseModel


class _AtlasRequestOverrides(BaseModel):
    """Per-request Atlas overrides (collection/index/field names)."""
    collection: str | None = None
    vector_index: str | None = None
    search_index: str | None = None
    text_key: str | None = None
    embedding_key: str | None = None
    dimensions: int | None = None


class _RetrievalRequestOverrides(BaseModel):
    """Per-request retrieval tuning (hybrid weights, num_candidates)."""
    vector_weight: float | None = None
    fulltext_weight: float | None = None
    num_candidates: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Agent Engine contract (custom reasoning engine template)
#
#   __init__()   runs locally  → must stay trivial (cloudpickle-safe)
#   set_up()     runs remotely → heavy lifting (build the SearchaaS container)
#   query()      served as the Agent Engine `query` API method
#   stream_query() served as the `streamQuery` API method
# ─────────────────────────────────────────────────────────────────────────────
class SearchaaSAgent:
    """
    Vertex AI Agent Engine wrapper for the SearchaaS retrieval platform.

    Exposes the full understand → plan → retrieve → summarize pipeline
    (identical to the FastAPI `/retrieve` and MCP `auto_search` surfaces) as a
    managed Agent Engine endpoint. The `searchaas` package is shipped via
    extra_packages; all provider credentials arrive as Secret Manager-backed
    environment variables.
    """

    def __init__(self) -> None:
        # Keep __init__ trivial — the instance is pickled locally and
        # unpickled in the remote runtime, where set_up() is then invoked.
        self._container = None

    def set_up(self) -> None:
        """Runs in the remote Agent Engine runtime before serving traffic."""
        from searchaas.app.bootstrap import build_container
        from searchaas.observability import configure_logging, get_logger

        configure_logging()
        log = get_logger("searchaas.agent_engine")
        log.info("Agent Engine set_up: building SearchaaS container…")
        self._container = build_container()
        log.info(
            "Agent Engine container ready (embeddings=%s llm=%s default_strategy=%s)",
            self._container.config.embeddings.provider,
            self._container.config.planner.llm_provider,
            self._container.config.retrieval.default_strategy,
        )

    # ── internals ────────────────────────────────────────────────────────────
    def _ensure_container(self):
        if self._container is None:
            self.set_up()
        return self._container

    def _execute(
        self,
        query: str,
        top_k: int | None,
        strategy: str | None,
        filters: dict | None,
        atlas: dict | None = None,
        retrieval: dict | None = None,
    ) -> dict:
        """understand → plan → retrieve → summarize (mirrors auto_search).

        atlas / retrieval are optional per-request override dicts matching the
        FastAPI /retrieve surface (collection, vector_index, search_index,
        text_key, embedding_key, dimensions / vector_weight, fulltext_weight,
        num_candidates). They let one engine serve multiple collections.
        """
        import re
        import time

        from searchaas.utils import (
            clamp_auto_strategy,
            filter_by_entities,
            serialize_docs,
            summarize,
        )

        c = self._ensure_container()
        t_total = time.perf_counter()

        # Per-request overrides (same semantics as FastAPI RetrieveRequest).
        overrides = (
            _AtlasRequestOverrides.model_validate(atlas) if atlas else None
        )
        retrieval_overrides = (
            _RetrievalRequestOverrides.model_validate(retrieval)
            if retrieval else None
        )

        t_uq = time.perf_counter()
        uq = c.understanding.process(query)
        understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

        # Merge NLU-extracted metadata filters with explicit request filters.
        merged_filters = {**(uq.metadata_filters or {}), **(filters or {})}

        t_plan = time.perf_counter()
        if strategy:
            plan = c.planner.plan_for(
                strategy=strategy, query=uq.rewritten,
                top_k=top_k or 20, filters=merged_filters,
            )
        else:
            plan = c.planner.plan(uq)
            plan.strategy = clamp_auto_strategy(plan.strategy, uq.intent)
            # A count named in the query wins; otherwise honor explicit top_k.
            if getattr(uq, "limit", None) is None and top_k:
                plan.top_k = top_k
            plan.filters = {**plan.filters, **merged_filters}
        planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)

        def _invoke():
            return c.retrievers.create(
                plan,
                overrides=overrides,
                retrieval_overrides=retrieval_overrides,
            ).invoke(uq.rewritten)

        t_mongo = time.perf_counter()
        try:
            docs = _invoke()
        except Exception as exc:
            # Atlas rejects $vectorSearch pre-filters on paths not declared as
            # {type: filter} in the index. Evict the bad field and retry once
            # without filters so the agent returns results rather than raising.
            err = str(exc)
            if "needs to be indexed as filter" in err and plan.filters:
                bad = re.search(r"Path '([^']+)' needs to be indexed", err)
                if bad:
                    c.retrievers.evict_filter_field(bad.group(1))
                plan.filters = {}
                docs = _invoke()
            else:
                raise
        mongo_ms = round((time.perf_counter() - t_mongo) * 1000, 1)

        results = serialize_docs(
            docs, c.config.atlas.embedding_key, include_score=True
        )
        results = filter_by_entities(results, list(uq.entities or []))

        t_sum = time.perf_counter()
        summary = (
            summarize(c.llm, uq.rewritten, results)
            if c.config.planner.enable_summarization
            else None
        )
        summarize_ms = (
            round((time.perf_counter() - t_sum) * 1000, 1)
            if summary is not None else None
        )

        total_ms = round((time.perf_counter() - t_total) * 1000, 1)
        return {
            "strategy": plan.strategy,
            "summary": summary,
            "results": results,
            "understood_query": {
                "raw": uq.raw,
                "corrected": uq.corrected,
                "rewritten": uq.rewritten,
                "entities": list(uq.entities or []),
                "metadata_filters": dict(uq.metadata_filters or {}),
                "intent": uq.intent,
            },
            "plan": plan.model_dump(),
            "timings": {
                "mongo_ms": mongo_ms,
                "planning_ms": planning_ms,
                "understanding_ms": understanding_ms,
                "summarize_ms": summarize_ms,
                "total_ms": total_ms,
            },
        }

    # ── Agent Engine API methods ─────────────────────────────────────────────
    def query(
        self,
        input: str,
        top_k: int = 20,
        strategy: str | None = None,
        filters: dict | None = None,
        atlas: dict | None = None,
        retrieval: dict | None = None,
    ) -> dict:
        """Run a natural-language search against MongoDB Atlas via SearchaaS.

        Args:
            input: Natural-language query, e.g. "best rated hotels in Paris".
            top_k: Maximum number of documents to retrieve (default 20).
            strategy: Optional fixed strategy — one of "vector", "fulltext",
                "hybrid", "graph", "parent_doc", "metadata". When omitted the
                planner picks a strategy automatically (auto mode).
            filters: Optional metadata pre-filters, e.g. {"imdb.rating": 8}.
            atlas: Optional per-request Atlas overrides —
                {collection, vector_index, search_index, text_key,
                embedding_key, dimensions}. Lets one engine serve multiple
                collections (same database as the engine config).
            retrieval: Optional per-request retrieval tuning —
                {vector_weight, fulltext_weight, num_candidates}.

        Returns:
            dict with strategy, summary, results, understood_query, plan,
            timings.
        """
        return self._execute(input, top_k, strategy, filters, atlas, retrieval)

    def stream_query(
        self,
        input: str,
        top_k: int = 20,
        strategy: str | None = None,
        filters: dict | None = None,
        atlas: dict | None = None,
        retrieval: dict | None = None,
    ):
        """Streaming variant of query — yields progress events then the result.

        Args:
            input: Natural-language query.
            top_k: Maximum number of documents to retrieve (default 20).
            strategy: Optional fixed strategy (see query()).
            filters: Optional metadata pre-filters.
            atlas: Optional per-request Atlas overrides (see query()).
            retrieval: Optional per-request retrieval tuning (see query()).

        Yields:
            Progress event dicts, ending with the full response dict.
        """
        yield {"status": "running", "step": "understand_and_plan", "input": input}
        result = self._execute(input, top_k, strategy, filters, atlas, retrieval)
        yield {
            "status": "running",
            "step": "retrieved",
            "strategy": result["strategy"],
            "num_results": len(result["results"]),
        }
        yield {"status": "completed", "result": result}


# ─────────────────────────────────────────────────────────────────────────────
# Deployment
# ─────────────────────────────────────────────────────────────────────────────
# Secret-backed env vars (values pushed to Secret Manager by deploy.sh and
# injected at runtime via SecretRef — never baked into the pickled agent).
SECRET_KEYS = (
    "ATLAS_URI",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_OPENAI_API_KEY",
)

# Plain (non-secret) config env vars forwarded from the deploy environment.
CONFIG_KEYS = (
    "ATLAS_DB",
    "ATLAS_COLLECTION",
    "ATLAS_VECTOR_INDEX",
    "ATLAS_SEARCH_INDEX",
    "ATLAS_TEXT_KEY",
    "ATLAS_EMBEDDING_KEY",
    "ATLAS_RELEVANCE_FN",
    "ATLAS_DIMENSIONS",
    "EMBEDDINGS_PROVIDER",
    "EMBEDDINGS_MODEL",
    "EMBEDDINGS_OUTPUT_DIMENSION",
    "PLANNER_LLM_PROVIDER",
    "PLANNER_MODEL",
    "PLANNER_TEMPERATURE",
    "PLANNER_DEFAULT_TOP_K",
    "RETRIEVAL_DEFAULT_STRATEGY",
    "RETRIEVAL_HYBRID_VECTOR_WEIGHT",
    "RETRIEVAL_HYBRID_FULLTEXT_WEIGHT",
    "RETRIEVAL_VECTOR_NUM_CANDIDATES",
    "LOG_LEVEL",
    "SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK",
    "AWS_DEFAULT_REGION",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
)

# Requirements never needed inside the Agent Engine runtime (test tooling).
EXCLUDE_REMOTE_REQS = ("pytest",)

# The managed runtime's own serving layer imports google.cloud.aiplatform
# (telemetry, exporters), so it must be present in the remote requirements
# even though the SearchaaS agent code never imports it.
REMOTE_EXTRA_REQS = ("google-cloud-aiplatform[agent_engines]>=1.93.0",)


def _remote_requirements(req_file: Path) -> list[str]:
    """Read the repo requirements.txt → pip spec list for the remote build."""
    reqs: list[str] = []
    for line in req_file.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if any(line.lower().startswith(pkg) for pkg in EXCLUDE_REMOTE_REQS):
            continue
        reqs.append(line)
    return reqs + [r for r in REMOTE_EXTRA_REQS
                   if not any(q.startswith(r.split("[")[0]) for q in reqs)]


def _build_env_vars() -> dict:
    """Assemble Agent Engine env_vars: str values for config, SecretRef dicts
    for secrets (resolved from Secret Manager by the managed runtime)."""
    env_vars: dict = {}
    for key in CONFIG_KEYS:
        val = os.environ.get(key)
        if val:
            env_vars[key] = val
    for key in SECRET_KEYS:
        if os.environ.get(key):
            env_vars[key] = {"secret": key, "version": "latest"}
    return env_vars


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy SearchaaS to Vertex AI Agent Engine (Reasoning Engine)."
    )
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", default="us-central1", help="Vertex AI region")
    parser.add_argument(
        "--staging-bucket", required=True,
        help="gs:// bucket used by Agent Engine to stage code/artifacts",
    )
    parser.add_argument("--display-name", default="searchaas-agent")
    parser.add_argument(
        "--engine-id", default=os.environ.get("AGENT_ENGINE_ID", ""),
        help="Existing Agent Engine numeric ID to UPDATE in place "
             "(default: create a new engine). Can also be set via AGENT_ENGINE_ID.",
    )
    args = parser.parse_args()

    import vertexai
    from vertexai import agent_engines

    vertexai.init(
        project=args.project,
        location=args.region,
        staging_bucket=args.staging_bucket,
    )

    requirements = _remote_requirements(REPO_ROOT / "requirements.txt")
    env_vars = _build_env_vars()

    # extra_packages paths are tarred with their relative names — run from the
    # repo root so `searchaas/` lands at the tar root and is importable
    # remotely as a top-level package.
    os.chdir(REPO_ROOT)

    create_kwargs = dict(
        agent_engine=SearchaaSAgent(),
        requirements=requirements,
        extra_packages=["searchaas"],
        env_vars=env_vars,
        display_name=args.display_name,
        description=(
            "SearchaaS retrieval agent — MongoDB Atlas vector/fulltext/hybrid/"
            "graph/parent-doc/metadata search with query understanding, "
            "LLM-planned strategy selection, and grounded summarization."
        ),
        resource_limits={"cpu": "2", "memory": "4Gi"},
    )

    if args.engine_id:
        resource_name = (
            f"projects/{args.project}/locations/{args.region}"
            f"/reasoningEngines/{args.engine_id}"
        )
        print(f"[INFO] Updating existing Agent Engine: {resource_name}")
        engine = agent_engines.get(resource_name)
        remote = engine.update(**create_kwargs)
    else:
        print("[INFO] Creating new Agent Engine…")
        remote = agent_engines.create(**create_kwargs)

    print("")
    print("AGENT_ENGINE_RESOURCE=" + remote.resource_name)
    print("AGENT_ENGINE_ID=" + remote.resource_name.rsplit("/", 1)[-1])


if __name__ == "__main__":
    main()
