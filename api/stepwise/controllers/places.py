"""Nearby destinations endpoint."""

from __future__ import annotations

import logging

from ..http.errors import Unprocessable
from ..http.request import Request
from ..http.response import Response
from ..models.location import Coordinate
from .base import Controller

LOG = logging.getLogger(__name__)

#: Cap on returned places, so a wide radius cannot produce a huge response.
MAX_RESULTS = 200


class PlacesController(Controller):
    """``GET /v1/places?lat=&lon=&radius_m=`` — named destinations near a point."""

    def handle(self, request: Request) -> Response:
        """List destinations within a radius of a coordinate."""
        coordinate = Coordinate(
            request.number(request.query.get("lat"), "lat", -90, 90),
            request.number(request.query.get("lon"), "lon", -180, 180),
        )
        radius_m = request.number(request.query.get("radius_m"), "radius_m", 50, 5000, 1200.0)

        key = request.query.get("region") or self.registry.region_for(coordinate)
        if key is None:
            raise Unprocessable(
                "coordinates outside every covered region", regions=self.registry.keys()
            )

        places = self.registry.datasets(key).places.within(coordinate, radius_m)
        return Response.ok({"region": key, "count": len(places), "places": places[:MAX_RESULTS]})
