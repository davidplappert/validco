"""Dispatching a request to a controller.

No web framework. The API is five routes over data already in memory, so
FastAPI or Flask would add cold-start time and a dependency tree to replace
about forty lines. This is those forty lines.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from ..logging_config import bind_request
from .errors import ApiError, NotFound
from .request import Request
from .response import Response

LOG = logging.getLogger("stepwise.api")


class Router:
    """Maps ``(method, path)`` to a controller and runs it."""

    def __init__(self) -> None:
        """Start with an empty route table."""
        self._routes: dict[tuple[str, str], Any] = {}
        self.cold_start = True

    def register(self, method: str, path: str, controller) -> Router:
        """Add one route. Returns self so registrations can chain."""
        self._routes[(method.upper(), path)] = controller
        return self

    def routes(self) -> list[str]:
        """Every registered route as ``"METHOD /path"``, for error messages."""
        return [f"{method} {path}" for method, path in sorted(self._routes)]

    def dispatch(self, event: dict[str, Any], context: Any = None) -> dict[str, Any]:
        """Handle one invocation end to end.

        Every exit path is logged with its status and duration, and every
        unexpected exception becomes a 500 carrying the request id — so a user
        reporting a failure hands over the exact key needed to find it in
        CloudWatch.
        """
        started = time.perf_counter()
        request = Request(event, context)
        request_id = bind_request(context, route=f"{request.method} {request.path}")

        LOG.info(
            "request received",
            extra={
                "method": request.method,
                "path": request.path,
                "cold_start": self.cold_start,
                "source_ip": request.identity.get("ip"),
                "user_agent": request.identity.get("user_agent"),
                "query": request.query,
                "body_bytes": len(event.get("body") or ""),
            },
        )

        try:
            # Preflight is normally answered by API Gateway itself; handling it
            # here too keeps the function correct when invoked directly.
            if request.method == "OPTIONS":
                response = Response.no_content()
            else:
                controller = self._routes.get(request.route_key)
                if controller is None:
                    raise NotFound(
                        f"no route for {request.method} {request.path}",
                        available=self.routes(),
                    )
                response = controller.handle(request)
        except ApiError as exc:
            LOG.warning(
                "request rejected",
                extra={"status": exc.status, "reason": exc.message, "detail": exc.detail},
            )
            response = Response(exc.status, {**exc.to_dict(), "request_id": request_id})
        except Exception as exc:  # noqa: BLE001 — last line of defence
            LOG.error(
                "unhandled exception",
                extra={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                exc_info=True,
            )
            response = Response(500, {"error": "internal error", "request_id": request_id})

        rendered = response.to_lambda()
        LOG.info(
            "request complete",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "cold_start": self.cold_start,
                "response_bytes": len(rendered.get("body") or ""),
            },
        )
        self.cold_start = False
        return rendered
