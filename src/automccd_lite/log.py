"""Structured logs with an allowlist.

Record text, evidence spans, certified terms, reviewer notes and names never
reach a log line. `log_event` drops every field that is not on the list, so
a careless call site cannot leak PHI by accident; it can only log less.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

ALLOWLIST = frozenset(
    {
        "record_id",
        "route",
        "reasons",
        "draft_status",
        "timings_ms",
        "usage",
        "model",
        "prompt_sha1",
        "icd_release",
        "status",
        "http_status",
        "error",  # the exception class name, never its message
    }
)

# Library loggers that print request URLs or headers at INFO. The icd-api
# URLs carry certified terms, sex and age (`autocode?searchText=...`,
# `doris?sex=...`), so these stay at WARNING whatever the root level is.
NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "openai", "azure", "urllib3", "sqlalchemy.engine")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            line.update(fields)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # URLs can carry ids; we log our own
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    kept = {k: v for k, v in fields.items() if k in ALLOWLIST}
    logger.log(level, event, extra={"fields": kept})
