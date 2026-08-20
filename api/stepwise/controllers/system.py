"""Liveness and coverage endpoints."""

from __future__ import annotations

import logging

from ..http.request import Request
from ..http.response import Response
from ..settings import SETTINGS
from .base import Controller

LOG = logging.getLogger(__name__)

ATTRIBUTION = (
    "Places, roads and addresses © Overture Maps Foundation, © OpenStreetMap "
    "contributors. Elevation from USGS 3DEP via AWS Terrain Tiles."
)


class RootController(Controller):
    """``GET /`` — send a person to the app, tell a machine where things are.

    The API's base URL is the address most likely to be pasted somewhere by
    hand — into a submission form, a ticket, a message — and until this existed
    it answered with a 404. A well-formed 404 with a list of routes, but still
    the impression that the thing is broken, which is the wrong first thing for
    anyone evaluating it to see.

    A redirect rather than a landing page because there is already a perfectly
    good front end; duplicating any of it here would be a second thing to keep
    true.
    """

    def handle(self, request: Request) -> Response:
        """Redirect to the app, or explain itself if there is no app to name."""
        site = SETTINGS.site_url
        if not site:
            # Local development, and any deployment without a configured site:
            # say what this is rather than pretending to redirect somewhere.
            LOG.debug("root requested with no site configured")
            return Response.ok(
                {
                    "service": "stepwise",
                    "api": "/v1",
                    "health": "/v1/health",
                    "message": "StepWise API. No browser app is configured for this deployment.",
                }
            )
        LOG.info("redirecting root to the app site=%s", site)
        return Response.redirect(site)


class HealthController(Controller):
    """``GET /v1/health`` — liveness, plus which datasets are resident."""

    def handle(self, request: Request) -> Response:
        """Report service identity and dataset residency.

        Reporting what is loaded makes cold-start behaviour observable rather
        than something to infer from latency graphs.
        """
        return Response.ok(
            {
                "ok": True,
                "service": "stepwise",
                **SETTINGS.describe(),
                "regions_registered": self.registry.status(),
            }
        )
