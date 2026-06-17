"""
Centralised logging for SearchaaS.

`configure_logging()` is idempotent and is called by every entrypoint
(`searchaas.api.app`, `searchaas.mcp_server.server`, `searchaas.diagnose`).
It uses dictConfig so library loggers (`pymongo`, `langchain_mongodb`,
`httpx`) can be tuned through env vars without code changes.

Env vars
--------
SEARCHAAS_LOG_LEVEL   : root level (default INFO)
SEARCHAAS_LOG_FORMAT  : "plain" (default) | "json"
PYMONGO_LOG_LEVEL     : default WARNING (set to INFO/DEBUG for wire traces)
LANGCHAIN_LOG_LEVEL   : default INFO
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Surface structured kwargs passed via `logger.info("...", extra={...})`
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "exc_info", "exc_text", "stack_info",
                     "name", "levelname", "levelno", "pathname", "filename",
                     "module", "lineno", "funcName", "created", "msecs",
                     "relativeCreated", "thread", "threadName", "processName",
                     "process", "message", "asctime"):
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_CONFIGURED = False


def configure_logging(force: bool = False) -> None:
    """Configure root + library loggers. Safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    level = os.environ.get("SEARCHAAS_LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("SEARCHAAS_LOG_FORMAT", "plain").lower()

    formatter: dict[str, Any]
    if fmt == "json":
        formatter = {"()": f"{__name__}._JsonFormatter"}
    else:
        formatter = {
            "format": "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            "datefmt": "%H:%M:%S",
        }

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": formatter},
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": sys.stderr,
                "formatter": "default",
            },
        },
        "loggers": {
            "searchaas":         {"level": level,                                            "handlers": ["stderr"], "propagate": False},
            "pymongo":           {"level": os.environ.get("PYMONGO_LOG_LEVEL", "WARNING"),   "handlers": ["stderr"], "propagate": False},
            "langchain_mongodb": {"level": os.environ.get("LANGCHAIN_LOG_LEVEL", "INFO"),    "handlers": ["stderr"], "propagate": False},
            "langchain":         {"level": os.environ.get("LANGCHAIN_LOG_LEVEL", "INFO"),    "handlers": ["stderr"], "propagate": False},
            "httpx":             {"level": "WARNING",                                        "handlers": ["stderr"], "propagate": False},
            "uvicorn.error":     {"level": "INFO",                                           "handlers": ["stderr"], "propagate": False},
            "uvicorn.access":    {"level": "INFO",                                           "handlers": ["stderr"], "propagate": False},
        },
        "root": {"level": level, "handlers": ["stderr"]},
    })
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
