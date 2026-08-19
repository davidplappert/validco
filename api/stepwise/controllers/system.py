"""Liveness and coverage endpoints."""

from __future__ import annotations

import logging
import os

from ..http.request import Request
from ..http.response import Response
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
                "version": os.environ.get("APP_VERSION", "dev"),
                "env": os.environ.get("ENV_NAME", "local"),
                "regions_registered": self.registry.status(),
            }
        )


class RegionsController(Controller):
    """``GET /v1/regions`` — the coverage areas and their dataset sizes."""

    def handle(self, request: Request) -> Response:
        """List every region, with the default the frontend should open on."""
        return Response.ok(
            {
                "regions": [r.to_dict() for r in self.registry.regions.values()],
                "default": self.registry.default_region_key,
                "attribution": ATTRIBUTION,
            }
        )
