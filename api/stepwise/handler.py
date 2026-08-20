"""Lambda entry point.

Nothing but wiring. The router is built once at import time — which, in Lambda,
means once per container — and every invocation is one call into it. All the
behaviour lives in ``stepwise.controllers``, ``stepwise.services`` and
``stepwise.models``.

Routes
------
``GET  /v1/health``    liveness plus which datasets are resident
``GET  /v1/regions``   coverage areas, ready and in progress
``POST /v1/regions``   request coverage of somewhere new; returns a poll URL
``GET  /v1/regions/{key}``     one region's state and build progress
``DELETE /v1/regions/{key}``   clear a failed build so it can be retried
``GET  /v1/geocode``   free-text address to coordinates, from Overture addresses
``GET  /v1/suggest``   address autocomplete as the user types
``GET  /v1/places``    named destinations near a point
``POST /v1/plan``      the product: profile plus start point to ranked walks

Cold start is handled by module-scope caching in
:mod:`stepwise.datasets.registry`: the first request in a container decodes the
arrays it touches, the rest reuse them. X-Ray is active on the function, so
those two populations show up as distinct latency modes rather than as
unexplained variance.
"""

from __future__ import annotations

import logging
from typing import Any

from .controllers import (
    GeocodeController,
    HealthController,
    PlacesController,
    PlanController,
    RegionDeleteController,
    RegionRequestController,
    RegionsController,
    RegionStatusController,
    RootController,
    SuggestController,
)
from .http.router import Router
from .logging_config import configure

configure()
LOG = logging.getLogger("stepwise.api")


def build_router() -> Router:
    """Assemble the route table.

    A function rather than module-level statements so a test can build a fresh
    router — with its own registry — without reimporting the module.
    """
    return (
        Router()
        .register("GET", "/", RootController())
        .register("GET", "/v1/health", HealthController())
        .register("GET", "/v1/regions", RegionsController())
        .register("POST", "/v1/regions", RegionRequestController())
        .register("GET", "/v1/regions/{key}", RegionStatusController())
        .register("DELETE", "/v1/regions/{key}", RegionDeleteController())
        .register("GET", "/v1/geocode", GeocodeController())
        .register("GET", "/v1/suggest", SuggestController())
        .register("GET", "/v1/places", PlacesController())
        .register("POST", "/v1/plan", PlanController())
    )


#: Built at import time so container reuse skips it entirely.
ROUTER = build_router()


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway proxy entry point."""
    return ROUTER.dispatch(event, context)
