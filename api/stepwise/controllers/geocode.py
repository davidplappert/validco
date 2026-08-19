"""Address lookup endpoint."""

from __future__ import annotations

import logging

from ..http.request import Request
from ..http.response import Response
from ..services.geocoder import Geocoder
from .base import Controller

LOG = logging.getLogger(__name__)


class GeocodeController(Controller):
    """``GET /v1/geocode?q=...`` — free text to a coordinate."""

    def handle(self, request: Request) -> Response:
        """Resolve a query against one region, or every region in turn.

        Probing regions linearly is fine at this scale and only touches each
        region's address container, not its graph. A total miss returns 404 with
        every region's suggestions attached, so the caller can offer a
        correction rather than a dead end.
        """
        query = request.required_query("q")
        keys = [request.query["region"]] if request.query.get("region") else self.registry.keys()

        attempts = []
        for key in keys:
            result = Geocoder(self.registry.datasets(key).addresses).resolve(query)
            result.region = key
            if result.found:
                return Response.ok(result.to_dict())
            attempts.append(result.to_dict())

        return Response(404, {"found": False, "query": query, "tried": attempts})
