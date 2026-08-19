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
