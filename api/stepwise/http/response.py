"""Outgoing API Gateway responses."""

from __future__ import annotations

import json
import os
from typing import Any


class Response:
    """A JSON response in the shape API Gateway's proxy integration expects."""

    @staticmethod
    def cors_headers() -> dict[str, str]:
        """CORS headers, resolved from the environment at call time.

        **Fails closed.** The frontend is served from a CloudFront domain that
        is only known once the stack deploys, so the allowed origin arrives as
        an environment variable. If that variable is missing in a *deployed*
        environment, no ``Access-Control-Allow-Origin`` header is emitted at
        all — a misconfiguration must not silently widen access to every origin
        on the internet, which is exactly what a ``"*"`` default would do.

        The wildcard survives only for local development, where ``ENV_NAME`` is
        unset and there is no browser origin worth protecting.

        Read per call rather than at import so a test can vary the environment
        without reloading the module.
        """
        configured = os.environ.get("CORS_ALLOW_ORIGIN", "").strip()
        deployed = bool(os.environ.get("ENV_NAME"))

        headers = {
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Access-Control-Max-Age": "86400",
            # The permitted origin is configuration rather than a constant, so
            # any cache in front of this must key on Origin.
            "Vary": "Origin",
        }
        if configured:
            headers["Access-Control-Allow-Origin"] = configured
        elif not deployed:
            headers["Access-Control-Allow-Origin"] = "*"
        return headers

    def __init__(self, status: int, body: Any, headers: dict[str, str] | None = None):
        """Bind the status, the body to serialise, and any extra headers."""
        self.status = status
        self.body = body
        self.headers = headers or {}

    @classmethod
    def ok(cls, body: Any) -> Response:
        """A 200 with a JSON body."""
        return cls(200, body)

    @classmethod
    def no_content(cls) -> Response:
        """A 204, used for CORS preflight."""
        return cls(204, "")

    def to_lambda(self) -> dict[str, Any]:
        """Render to the dict API Gateway expects from a proxy integration."""
        return {
            "statusCode": self.status,
            "headers": {
                "content-type": "application/json",
                **self.cors_headers(),
                **self.headers,
            },
            "body": json.dumps(self.body, separators=(",", ":"), default=str),
        }
