"""Outgoing API Gateway responses."""

from __future__ import annotations

import json
import os
from typing import Any


class Response:
    """A JSON response in the shape API Gateway's proxy integration expects."""

    #: The frontend is served from a CloudFront domain only known after the
    #: stack deploys, so the allowed origin is injected as an environment
    #: variable and falls back to "*" for local development.
    @staticmethod
    def cors_headers() -> dict[str, str]:
        """CORS headers, read from the environment at call time.

        Read per-call rather than at import so a test can vary the environment
        without reloading the module.
        """
        return {
            "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Max-Age": "86400",
        }

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
