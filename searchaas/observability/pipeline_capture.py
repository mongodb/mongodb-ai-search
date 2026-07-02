"""
Capture the ACTUAL MongoDB aggregation pipeline executed during a retrieval.

Every retrieval strategy — our custom retrievers and the langchain-mongodb ones
alike — ultimately calls ``<collection>.aggregate(pipeline)``. Rather than
reconstruct an approximation of that pipeline (which drifts from reality), we
register a pymongo ``CommandListener`` that records the real ``pipeline`` of each
``aggregate`` command while a retrieval is in flight.

Why global registration: the langchain vector store builds its OWN ``MongoClient``
(``MongoDBAtlasVectorSearch.from_connection_string`` in bootstrap), separate from
``AtlasFactory.client()``. A per-client listener would miss those aggregates.
``monitoring.register(...)`` applies to every client created afterwards, so this
module must be imported before any client is constructed.

Scoping: capture is only active inside the ``capture()`` context manager (set
around ``retriever.invoke()``), and only ``aggregate`` commands are recorded — so
the ``find_one``/``update_one`` issued by FactStore/PolicyStore, and the
understanding/planning round-trips, are never captured.
"""
from __future__ import annotations

import contextvars
import datetime
from typing import Any

from pymongo import monitoring

from searchaas.observability import get_logger

log = get_logger("searchaas.pipeline_capture")

# Per-request sink. `None` means "not capturing"; the listener no-ops in that case.
_sink: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "pipeline_capture_sink", default=None
)

# Numeric arrays longer than this are summarised (a client-side queryVector is
# hundreds/thousands of floats — noise in the UI panel).
_MAX_ARRAY = 24


class _Capture:
    """Context manager that activates pipeline capture for the current context."""

    def __enter__(self) -> "_Capture":
        self._token = _sink.set([])
        return self

    def __exit__(self, *_exc: Any) -> None:
        _sink.reset(self._token)


def capture() -> _Capture:
    """Activate capture for the duration of the ``with`` block."""
    return _Capture()


def captured() -> list[dict[str, Any]]:
    """Return the aggregates captured in the active context (empty if none/inactive)."""
    return list(_sink.get() or [])


def _jsonify(value: Any) -> Any:
    """Recursively convert a BSON/SON pipeline into JSON-safe types.

    Long numeric arrays (query vectors) are collapsed to a placeholder so the
    rendered pipeline stays readable.
    """
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        seq = list(value)
        if len(seq) > _MAX_ARRAY and all(isinstance(x, (int, float, bool)) for x in seq):
            return f"<{len(seq)} floats>"
        return [_jsonify(v) for v in seq]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    # ObjectId, Decimal128, and any other BSON scalar -> string.
    return str(value)


class PipelineCaptureListener(monitoring.CommandListener):
    """Records the pipeline of every ``aggregate`` command while capture is active."""

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        if event.command_name != "aggregate":
            return
        sink = _sink.get()
        if sink is None:
            return
        pipeline = event.command.get("pipeline")
        if pipeline is None:
            return
        try:
            sink.append({
                "collection": event.command.get("aggregate"),
                "database": event.database_name,
                "pipeline": _jsonify(pipeline),
            })
        except Exception as exc:  # capture must never break a real query
            log.debug("pipeline capture failed: %s", exc)

    def succeeded(self, _event: monitoring.CommandSucceededEvent) -> None:  # noqa: D401
        pass

    def failed(self, _event: monitoring.CommandFailedEvent) -> None:  # noqa: D401
        pass


# Register globally, exactly once per process, at import time — before any
# MongoClient is built. The module-cache makes import idempotent, but the guard
# also protects against importlib.reload during dev/tests.
_REGISTERED = False


def _register_once() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    monitoring.register(PipelineCaptureListener())
    _REGISTERED = True
    log.debug("Pipeline capture listener registered")


_register_once()
