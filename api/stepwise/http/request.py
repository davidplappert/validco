"""An incoming API Gateway event, normalised and validated.

API Gateway has two payload formats: REST APIs (v1) send ``httpMethod``/``path``
while HTTP APIs (v2) send ``requestContext.http.method``/``rawPath``. This stack
deploys a REST API — it is the variant supporting X-Ray tracing and full
request/response logging at the gateway — but both are accepted, which keeps the
function portable and makes it trivial to construct in tests.

The validation helpers live here so controllers never hand-roll a type check or
a range check, and so every validation failure comes back with the same shape.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from .errors import BadRequest

LOG = logging.getLogger(__name__)


class Request:
    """One HTTP request, normalised across API Gateway payload formats."""

    def __init__(self, event: dict[str, Any], context: Any = None):
        """Parse an API Gateway event into method, path, query and body."""
        self.event = event
        self.context = context
        ctx = event.get("requestContext") or {}
        http = ctx.get("http") or {}

        if http:  # payload format 2.0
            self.method = str(http.get("method", "GET")).upper()
            raw_path = event.get("rawPath") or http.get("path") or "/"
            identity = {"ip": http.get("sourceIp"), "user_agent": http.get("userAgent")}
        else:  # payload format 1.0 (REST proxy)
            self.method = str(event.get("httpMethod", "GET")).upper()
            raw_path = event.get("path") or "/"
            ident = ctx.get("identity") or {}
            identity = {"ip": ident.get("sourceIp"), "user_agent": ident.get("userAgent")}

        self.path = self._strip_stage(raw_path, ctx.get("stage"))
        self.identity = identity
        self.query: dict[str, Any] = event.get("queryStringParameters") or {}
        self._body: dict[str, Any] | None = None

    @staticmethod
    def _strip_stage(path: str, stage: str | None) -> str:
        """Remove the deployment stage prefix and any trailing slash.

        A REST API deployed to a named stage prefixes every path with it. The
        route table describes the API's own shape, not where it happens to be
        deployed, so the prefix is stripped here.
        """
        if stage and stage != "$default" and path.startswith(f"/{stage}/"):
            path = path[len(stage) + 1 :]
        return path.rstrip("/") or "/"

    @property
    def route_key(self) -> tuple[str, str]:
        """The ``(method, path)`` pair the router dispatches on."""
        return self.method, self.path

    def json(self) -> dict[str, Any]:
        """The request body as a dict, parsed once and cached."""
        if self._body is None:
            raw = self.event.get("body") or "{}"
            if self.event.get("isBase64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8")
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise BadRequest(f"body is not valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise BadRequest("body must be a JSON object")
            self._body = parsed
        return self._body

    # --- validation helpers ------------------------------------------------

    @staticmethod
    def number(
        value: Any, name: str, low: float, high: float, default: float | None = None
    ) -> float:
        """Coerce a value to a float within a range, or raise :class:`BadRequest`.

        The error names the field and the accepted range, because "invalid
        input" is not something a caller can act on.
        """
        if value is None or value == "":
            if default is None:
                raise BadRequest(f"{name} is required")
            return default
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise BadRequest(f"{name} must be a number, got {value!r}") from exc
        if not low <= number <= high:
            raise BadRequest(f"{name} must be between {low} and {high}, got {number}")
        return number

    @classmethod
    def integer(cls, value: Any, name: str, low: int, high: int, default: int | None = None) -> int:
        """Coerce a value to an int within a range, or raise :class:`BadRequest`."""
        return int(cls.number(value, name, low, high, default))

    def required_query(self, name: str) -> str:
        """Fetch a query parameter that must be present and non-empty."""
        value = (self.query.get(name) or "").strip()
        if not value:
            raise BadRequest(f"{name} is required")
        return value

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return f"Request({self.method} {self.path})"
