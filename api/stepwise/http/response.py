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

    @classmethod
    def redirect(cls, location: str, permanent: bool = False) -> Response:
        """A redirect, carrying a JSON body for anything that is not a browser.

        302 rather than 301 by default: a permanent redirect is cached by
        browsers indefinitely and is painful to undo if the target ever moves,
        which for a CloudFront domain that is regenerated per stack is a real
        possibility rather than a hypothetical one.

        The body matters as much as the header. A person pasting this URL gets
        sent to the app; a script, or `curl` without `-L`, gets a document
        saying where the app is and where the API lives — rather than a blank
        page, which is what a bare redirect looks like to anything that does
        not follow it.
        """
        return cls(
            301 if permanent else 302,
            {
                "service": "stepwise",
                "app": location,
                "api": "/v1",
                "health": "/v1/health",
                "message": (
                    f"The StepWise app is at {location}. This host serves its API under /v1."
                ),
            },
            {"Location": location, "Cache-Control": "no-cache"},
        )

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
