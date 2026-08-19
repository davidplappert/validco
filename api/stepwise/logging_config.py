"""Structured DEBUG logging for the Lambda.

The brief for this project was "logging as debug as possible", so the default
level is DEBUG everywhere and every record is emitted as one JSON object per
line. That combination is what makes CloudWatch Logs Insights actually usable —
you can query on ``request_id``, ``route``, ``duration_ms`` or any extra field
without regex-scraping a text format.

Two AWS-specific details are handled here rather than at each call site:

* **The X-Ray trace id** is pulled from ``_X_AMZN_TRACE_ID`` and attached to
  every record, so a log line can be pivoted straight to its trace.
* **The Lambda request id** is bound once per invocation via
  :func:`bind_request`, so handler code never has to thread it through.

Emitting DEBUG for every request is a deliberate choice for a demo app and is
called out in the README as something to dial back under real traffic — the
free tier covers 5 GB of ingest a month, which this will not come close to, but
the reasoning should not be invisible.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

# Bound per invocation so every record can be correlated without plumbing.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_route: ContextVar[str] = ContextVar("route", default="-")

# Fields the logging module puts on every record; anything else the caller
# passed via `extra=` is application data and belongs in the JSON output.
_STANDARD = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with request correlation attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _request_id.get(),
            "trace_id": _trace_id.get(),
            "route": _route.get(),
            "loc": f"{record.module}:{record.lineno}",
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    """Keep the formatter total — a log call must never be the thing that fails."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value][:50]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in list(value.items())[:50]}
    return str(value)


def configure(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger.

    Lambda's runtime pre-installs its own handler, so we replace rather than add
    — otherwise every line is emitted twice, once JSON and once plain.
    """
    level = (level or os.environ.get("LOG_LEVEL") or "DEBUG").upper()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.DEBUG))

    # botocore at DEBUG logs every wire byte, which buries the application log
    # and inflates ingest for no diagnostic value here.
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        "logging configured", extra={"level": level, "python": sys.version.split()[0]}
    )


def bind_request(context: Any = None, route: str = "-") -> str:
    """Bind correlation ids for one invocation. Returns the request id."""
    rid = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    _request_id.set(rid)
    _route.set(route)

    # Format: Root=1-5759e988-...;Parent=...;Sampled=1 — the Root part is the
    # trace id the X-Ray console indexes on.
    raw = os.environ.get("_X_AMZN_TRACE_ID", "")
    trace = "-"
    for part in raw.split(";"):
        if part.startswith("Root="):
            trace = part[5:]
            break
    _trace_id.set(trace)
    return rid


def set_route(route: str) -> None:
    _route.set(route)


class Timer:
    """Context manager that logs how long a block took.

    Used around the expensive stages (dataset load, Dijkstra, scoring) so a slow
    request can be attributed from the logs alone, without a profiler.
    """

    def __init__(self, logger: logging.Logger, label: str, **fields: Any):
        self.log = logger
        self.label = label
        self.fields = fields
        self.started = 0.0
        self.duration_ms = 0.0

    def __enter__(self) -> Timer:
        self.started = time.perf_counter()
        self.log.debug("start %s", self.label, extra={"stage": self.label, **self.fields})
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.duration_ms = (time.perf_counter() - self.started) * 1000.0
        self.log.debug(
            "done %s in %.1fms",
            self.label,
            self.duration_ms,
            extra={
                "stage": self.label,
                "duration_ms": round(self.duration_ms, 1),
                "failed": exc is not None,
                **self.fields,
            },
        )
        return False
