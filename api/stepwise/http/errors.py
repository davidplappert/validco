"""Failures that carry an HTTP status and a message meant for the caller.

Modelled as a small hierarchy so a controller can `raise BadRequest(...)` and
never think about status codes, and so the router can distinguish "the caller
did something wrong" from "we did".
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Base class for failures with a status code and a caller-facing message."""

    status = 500

    def __init__(self, message: str, **detail: Any):
        """Bind the message and any structured detail to return alongside it."""
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        """Serialise as the error response body."""
        return {"error": self.message, **self.detail}


class BadRequest(ApiError):
    """The request was malformed or failed validation."""

    status = 400


class NotFound(ApiError):
    """The route does not exist, or the thing asked for could not be found."""

    status = 404


class Unprocessable(ApiError):
    """Well-formed, but the app cannot act on it.

    Used for the geographic cases — a point outside coverage, or a start with no
    walkable network near it. These are not the caller's mistake; they are the
    honest limits of the data.
    """

    status = 422
