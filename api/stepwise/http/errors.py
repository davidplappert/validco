"""Failures, modelled so they are useful to three different audiences.

An error here carries three things at once:

* **A machine-readable ``code``**, so the frontend can branch on the *kind* of
  failure rather than pattern-matching English prose.
* **A human ``title`` and ``detail``**, written to be shown to a person. "We
  don't have walking data for that area yet" is a sentence someone can act on;
  "422 Unprocessable Entity" is not.
* **A structured ``action``** where recovery is possible — the not-covered case
  hands the client everything it needs to offer an "add this area" button.

Everything technical stays in the logs, correlated by ``request_id``. The
distinction matters: a user needs to know what to do next, and an operator needs
to know what broke, and those are rarely the same sentence.
"""

from __future__ import annotations

from typing import Any


class ErrorAction:
    """A recovery step the client can offer the user."""

    def __init__(self, kind: str, label: str, **params: Any):
        """Describe an action by its kind, its button text, and its parameters."""
        self.kind = kind
        self.label = label
        self.params = params

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {"kind": self.kind, "label": self.label, **self.params}


class ApiError(Exception):
    """Base class for failures that are safe to describe to the caller."""

    status = 500
    code = "internal_error"
    title = "Something went wrong on our end"
    detail = "This one is on us. Try again in a moment."

    def __init__(
        self,
        message: str | None = None,
        *,
        title: str | None = None,
        detail: str | None = None,
        action: ErrorAction | None = None,
        code: str | None = None,
        **context: Any,
    ):
        """Build an error, overriding any of the class defaults.

        ``message`` is the developer-facing summary — it goes in the logs and in
        the ``error`` field. ``title`` and ``detail`` are what a person reads.
        """
        self.message = message or self.title
        super().__init__(self.message)
        if title is not None:
            self.title = title
        if detail is not None:
            self.detail = detail
        if code is not None:
            self.code = code
        self.action = action
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        """Serialise as the error response body."""
        payload: dict[str, Any] = {
            "error": self.message,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            **self.context,
        }
        if self.action is not None:
            payload["action"] = self.action.to_dict()
        return payload


class BadRequest(ApiError):
    """The request was malformed or failed validation."""

    status = 400
    code = "bad_request"
    title = "That request didn't look right"
    detail = "Check the highlighted fields and try again."


class NotFound(ApiError):
    """The route, or the thing asked for, does not exist."""

    status = 404
    code = "not_found"
    title = "We couldn't find that"
    detail = "Double-check the spelling, or try something nearby."


class Unprocessable(ApiError):
    """Well-formed, but the app cannot act on it.

    Used for the geographic cases — a point outside coverage, or a start with no
    walkable network nearby. These are the honest limits of the data rather than
    a caller mistake, and each one carries an action where recovery exists.
    """

    status = 422
    code = "unprocessable"
    title = "We can't plan a walk from there"
    detail = "Try a different starting point."


class Conflict(ApiError):
    """The resource exists but is not ready yet.

    Used when a region is still being extracted: the answer is "wait", not
    "fail", and the client should poll rather than retry blindly.
    """

    status = 409
    code = "not_ready"
    title = "Almost there"
    detail = "We're still preparing this area."


class TooLarge(BadRequest):
    """The request exceeded a size limit."""

    code = "too_large"
    title = "That request was too big"
    detail = "Try a shorter address."


class UpstreamUnavailable(ApiError):
    """A dependency failed in a way the caller can only wait out."""

    status = 503
    code = "upstream_unavailable"
    title = "A service we rely on is having trouble"
    detail = "This is usually brief. Please try again shortly."


# --- specific, reusable failures -------------------------------------------


class AddressNotFound(NotFound):
    """The geocoder could not resolve what the user typed."""

    code = "address_not_found"
    title = "We couldn't find that address"
    detail = (
        "It might be spelled differently in the map data, or be outside the "
        "areas we currently cover."
    )


class RegionNotCovered(Unprocessable):
    """The location is real, but no coverage exists for it yet.

    The most important error in the product, because it is the one with a good
    answer: offer to build the area.
    """

    code = "region_not_covered"
    title = "We don't have walking data for that area yet"
    detail = "We can pull it from Overture Maps now — it takes a minute or two."


class RegionBuilding(Conflict):
    """Coverage for this area is being extracted right now."""

    code = "region_building"
    title = "Preparing this area"
    detail = "We're downloading the walking network. This usually takes a minute or two."


class RegionBuildFailed(Unprocessable):
    """A previous attempt to build this area failed."""

    code = "region_build_failed"
    title = "We couldn't prepare that area"
    detail = "Overture may not cover it in enough detail. Try a nearby town or city."


class NoWalkableNetwork(Unprocessable):
    """There is coverage, but nothing walkable near the start point."""

    code = "no_walkable_network"
    title = "No walkable streets near that spot"
    detail = (
        "We couldn't find a mapped footpath or street within a few hundred "
        "metres. Try an address closer to a road."
    )
