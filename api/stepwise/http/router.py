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
        # Templated routes, kept separate so exact matches stay a dict lookup
        # and only fall through to a scan when nothing matched.
        self._templates: list[tuple[str, list[str], Any]] = []
        self.cold_start = True

    def register(self, method: str, path: str, controller) -> Router:
        """Add one route. Returns self so registrations can chain.

        A path may contain ``{name}`` segments, which are matched positionally
        and passed to the controller as ``request.path_params``.
        """
        method = method.upper()
        if "{" in path:
            segments = path.strip("/").split("/")
            names = [s[1:-1] for s in segments if s.startswith("{")]
            self._templates.append((method, segments, controller))
            self._routes[(method, path)] = controller  # so routes() lists it
            LOG.debug("registered templated route %s %s params=%s", method, path, names)
        else:
            self._routes[(method, path)] = controller
        return self

    def resolve(self, method: str, path: str) -> tuple[Any, dict[str, str]] | None:
        """Find the controller for a request, with any path parameters.

        Exact matches win over templates, so ``/v1/regions`` is never captured
        by ``/v1/regions/{key}``.
        """
        exact = self._routes.get((method, path))
        if exact is not None and "{" not in path:
            return exact, {}

        parts = path.strip("/").split("/")
        for route_method, segments, controller in self._templates:
            if route_method != method or len(segments) != len(parts):
                continue
            params: dict[str, str] = {}
            for segment, value in zip(segments, parts, strict=True):
                if segment.startswith("{") and segment.endswith("}"):
                    params[segment[1:-1]] = value
                elif segment != value:
                    break
            else:
                return controller, params
        return None

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
                match = self.resolve(request.method, request.path)
                if match is None:
                    raise NotFound(
                        f"no route for {request.method} {request.path}",
                        available=self.routes(),
                    )
                controller, request.path_params = match
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
