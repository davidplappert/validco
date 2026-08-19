"""Lambda entry point — an API Gateway router.

Deliberately no web framework. The whole API is five routes over data that is
already in memory, so FastAPI or Flask would add cold-start time and a
dependency tree to save about forty lines of dispatch. Everything here is
standard library plus this package.

Routes
------
``GET  /v1/health``    liveness plus which datasets are loaded
``GET  /v1/regions``   coverage areas and their sizes
``GET  /v1/geocode``   free-text address -> coordinates, from Overture addresses
``POST /v1/plan``      the product: profile + start point -> ranked walks
``GET  /v1/places``    named destinations near a point

Cold start is handled by module-scope caching in :mod:`stepwise.graph`. The
first request in a container pays to decode the arrays it touches; the rest are
free. X-Ray tracing is enabled on the function, so those two populations are
visible as distinct latency modes rather than as unexplained variance.
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from typing import Any

from . import graph as graph_mod
from .config import FLAG_LABELS, SURFACE_LABELS
from .geocode import geocode
from .health import Profile, ft_in_to_cm, health_effects, lb_to_kg
from .logging_config import Timer, bind_request, configure, set_route
from .routing import Planner, Preferences, elevation_profile, route_geojson

configure()
LOG = logging.getLogger("stepwise.api")

# Bounds that keep one request from monopolising the function. They are product
# limits as much as safety limits: a 4-hour walk is not what this app is for.
MIN_MINUTES = 5.0
MAX_MINUTES = 240.0
MAX_ROUTES = 6

CORS_HEADERS = {
    # The frontend is served from a CloudFront domain that is only known after
    # the stack deploys, so the allowed origin is injected as an env var and
    # falls back to "*" for local development.
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Max-Age": "86400",
}

COLD_START = True


class ApiError(Exception):
    """A failure with a status code and a message meant for the caller."""

    def __init__(self, status: int, message: str, **detail: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def respond(status: int, body: Any, extra_headers: dict | None = None) -> dict:
    headers = {"content-type": "application/json", **CORS_HEADERS}
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body, separators=(",", ":"), default=str),
    }


# --- request parsing -------------------------------------------------------


def _json_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, f"body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApiError(400, "body must be a JSON object")
    return parsed


def _number(value: Any, name: str, lo: float, hi: float, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ApiError(400, f"{name} is required")
        return default
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"{name} must be a number, got {value!r}") from exc
    if not lo <= num <= hi:
        raise ApiError(400, f"{name} must be between {lo} and {hi}, got {num}")
    return num


def build_profile(raw: dict) -> Profile:
    """Validate the health inputs and construct a :class:`Profile`.

    Weight and height are accepted in either unit system, because a US user
    thinks in pounds and feet and the model works in kilograms and centimetres.
    Doing the conversion here keeps the physiology code unit-clean.
    """
    if not isinstance(raw, dict):
        raise ApiError(400, "profile must be an object")

    sex = str(raw.get("sex", "")).strip().lower()
    if sex in ("m", "male", "man"):
        sex = "male"
    elif sex in ("f", "female", "woman"):
        sex = "female"
    else:
        raise ApiError(
            400,
            "profile.sex must be 'male' or 'female'",
            note="The gait-speed and body-composition norms this model "
            "uses are only published split by binary sex; that is a "
            "limitation of the source literature, not a design choice.",
        )

    age = int(_number(raw.get("age"), "profile.age", 13, 110))

    if raw.get("weight_kg") is not None:
        weight_kg = _number(raw.get("weight_kg"), "profile.weight_kg", 25, 400)
    elif raw.get("weight_lb") is not None:
        weight_kg = lb_to_kg(_number(raw.get("weight_lb"), "profile.weight_lb", 55, 880))
    else:
        raise ApiError(400, "profile needs weight_kg or weight_lb")

    height_cm = None
    if raw.get("height_cm") is not None:
        height_cm = _number(raw.get("height_cm"), "profile.height_cm", 120, 230)
    elif raw.get("height_ft") is not None:
        height_cm = ft_in_to_cm(
            _number(raw.get("height_ft"), "profile.height_ft", 3, 8),
            _number(raw.get("height_in"), "profile.height_in", 0, 11.9, 0.0),
        )

    profile = Profile(sex=sex, age_years=age, weight_kg=weight_kg, height_cm=height_cm)
    LOG.debug("profile built", extra={"profile": profile.describe()})
    return profile


def _resolve_start(body: dict) -> tuple[float, float, str, dict]:
    """Work out where the walk starts, and which region that lands in."""
    region_key = body.get("region")

    if body.get("lat") is not None and body.get("lon") is not None:
        lat = _number(body.get("lat"), "lat", -90, 90)
        lon = _number(body.get("lon"), "lon", -180, 180)
        key = region_key or graph_mod.region_for_point(lat, lon)
        if key is None:
            raise ApiError(
                422,
                "those coordinates are outside every covered region",
                regions=[r["key"] for r in graph_mod.manifest()["regions"]],
            )
        return lat, lon, key, {"match": "coordinates", "label": f"{lat:.5f}, {lon:.5f}"}

    address = (body.get("address") or "").strip()
    if not address:
        raise ApiError(400, "provide either address, or lat and lon")

    # Try the named region first; otherwise try each until one resolves. With two
    # regions a linear probe is simplest and cheapest — and each probe only
    # touches that region's address container.
    candidates = [region_key] if region_key else [r["key"] for r in graph_mod.manifest()["regions"]]
    misses: dict[str, Any] = {}
    for key in candidates:
        try:
            index = graph_mod.region(key).addresses
        except KeyError as exc:
            raise ApiError(400, str(exc)) from exc
        result = geocode(index, address)
        if result.get("found"):
            return result["lat"], result["lon"], key, result
        misses[key] = result.get("suggestions") or result.get("reason")

    raise ApiError(
        404,
        f"could not find {address!r} in any covered region",
        suggestions=misses,
        regions=[r["key"] for r in graph_mod.manifest()["regions"]],
    )


# --- routes ----------------------------------------------------------------


def route_health(event: dict) -> dict:
    loaded = {
        k: {"graph": v._graph is not None, "addresses": v._addresses is not None}
        for k, v in graph_mod._REGIONS.items()
    }
    return respond(
        200,
        {
            "ok": True,
            "service": "stepwise",
            "version": os.environ.get("APP_VERSION", "dev"),
            "cold_start": COLD_START,
            "regions_registered": loaded,
        },
    )


def route_regions(event: dict) -> dict:
    man = graph_mod.manifest()
    return respond(
        200,
        {
            "regions": man["regions"],
            "default": man["default_region"],
            "attribution": "Places, roads and addresses © Overture Maps Foundation, "
            "© OpenStreetMap contributors. Elevation from USGS 3DEP via "
            "AWS Terrain Tiles.",
        },
    )


def route_geocode(event: dict) -> dict:
    params = event.get("queryStringParameters") or {}
    query = (params.get("q") or "").strip()
    if not query:
        raise ApiError(400, "q is required")

    keys = (
        [params["region"]]
        if params.get("region")
        else [r["key"] for r in graph_mod.manifest()["regions"]]
    )
    results = []
    for key in keys:
        try:
            index = graph_mod.region(key).addresses
        except KeyError as exc:
            raise ApiError(400, str(exc)) from exc
        hit = geocode(index, query)
        hit["region"] = key
        results.append(hit)
        if hit.get("found"):
            return respond(200, hit)
    return respond(404, {"found": False, "query": query, "tried": results})


def route_places(event: dict) -> dict:
    params = event.get("queryStringParameters") or {}
    lat = _number(params.get("lat"), "lat", -90, 90)
    lon = _number(params.get("lon"), "lon", -180, 180)
    radius = _number(params.get("radius_m"), "radius_m", 50, 5000, 1200.0)
    key = params.get("region") or graph_mod.region_for_point(lat, lon)
    if key is None:
        raise ApiError(422, "coordinates outside every covered region")
    places = graph_mod.region(key).places.within(lat, lon, radius)
    return respond(200, {"region": key, "count": len(places), "places": places[:200]})


def route_plan(event: dict) -> dict:
    """The main endpoint: turn a person and a time budget into walks worth taking."""
    body = _json_body(event)
    profile = build_profile(body.get("profile") or {})
    minutes = _number(body.get("minutes"), "minutes", MIN_MINUTES, MAX_MINUTES, 30.0)
    max_routes = int(_number(body.get("max_routes"), "max_routes", 1, MAX_ROUTES, 4))
    prefs = Preferences.from_dict(body.get("preferences"))

    with Timer(LOG, "resolve_start"):
        lat, lon, region_key, origin = _resolve_start(body)

    data = graph_mod.region(region_key)
    with Timer(LOG, "load_graph", region=region_key):
        walk_graph = data.graph

    with Timer(LOG, "snap_to_network"):
        snapped = walk_graph.nearest_node(lat, lon, max_m=600.0)
    if snapped is None:
        raise ApiError(
            422,
            "no walkable street or path within 600 m of that location",
            lat=lat,
            lon=lon,
            region=region_key,
        )
    start_node, snap_m = snapped

    planner = Planner(walk_graph, places=data.places, green=data.green)
    with Timer(LOG, "plan_routes", target_minutes=minutes) as timer:
        routes = planner.plan(profile, start_node, minutes, prefs, max_routes=max_routes)
    plan_ms = timer.duration_ms

    if not routes:
        raise ApiError(
            422,
            "could not build a walk from there — the network around that point is "
            "too sparse or disconnected",
            lat=lat,
            lon=lon,
            region=region_key,
            snap_distance_m=round(snap_m),
        )

    with Timer(LOG, "serialise", routes=len(routes)):
        payload = [_serialise_route(walk_graph, profile, r, i) for i, r in enumerate(routes)]

    return respond(
        200,
        {
            "region": region_key,
            "origin": {
                "lat": lat,
                "lon": lon,
                "snapped_lat": walk_graph.node_lat[start_node],
                "snapped_lon": walk_graph.node_lon[start_node],
                "snap_distance_m": round(snap_m),
                **{k: v for k, v in origin.items() if k in ("label", "match", "address")},
            },
            "profile": profile.describe(),
            "request": {"minutes": minutes, "preferences": prefs.to_dict()},
            "routes": payload,
            "timing_ms": {"plan": round(plan_ms, 1)},
            "attribution": "Overture Maps Foundation / OpenStreetMap contributors; "
            "elevation USGS 3DEP via AWS Terrain Tiles.",
        },
    )


def _serialise_route(walk_graph, profile: Profile, route, index: int) -> dict:
    effort = route.effort.to_dict()
    flags = 0
    for edge, _ in route.legs:
        flags |= walk_graph.edge_flags[edge]

    return {
        "id": index,
        "shape": route.shape,
        "score": route.score,
        "destination": route.anchor,
        "effort": effort,
        "health": health_effects(profile, route.effort),
        "suitability": route.suitability,
        "surface_breakdown_pct": route.surface_breakdown,
        "surface_labels": {walk_graph.surfaces[i]: label for i, label in SURFACE_LABELS.items()},
        "features": [label for bit, label in FLAG_LABELS.items() if flags & bit],
        "streets": route.streets[:25],
        "geometry": route_geojson(walk_graph, route),
        "elevation_profile": elevation_profile(walk_graph, route),
    }


ROUTES = {
    ("GET", "/v1/health"): route_health,
    ("GET", "/v1/regions"): route_regions,
    ("GET", "/v1/geocode"): route_geocode,
    ("GET", "/v1/places"): route_places,
    ("POST", "/v1/plan"): route_plan,
}


def _normalise_request(event: dict) -> tuple[str, str, dict]:
    """Extract method, path and HTTP context from either payload format.

    REST APIs (v1) send ``httpMethod``/``path``; HTTP APIs (v2) send
    ``requestContext.http.method``/``rawPath``. We deploy a REST API because it
    is the variant that supports X-Ray tracing and full request/response
    execution logging at the gateway, but supporting both keeps the function
    testable and portable if that trade ever changes.
    """
    ctx = event.get("requestContext") or {}
    http = ctx.get("http") or {}

    if http:  # payload format 2.0
        method = http.get("method", "GET")
        path = event.get("rawPath") or http.get("path") or "/"
        identity = {"sourceIp": http.get("sourceIp"), "userAgent": http.get("userAgent")}
    else:  # payload format 1.0 (REST API proxy)
        method = event.get("httpMethod", "GET")
        path = event.get("path") or "/"
        ident = ctx.get("identity") or {}
        identity = {"sourceIp": ident.get("sourceIp"), "userAgent": ident.get("userAgent")}

    # A stage prefix appears when the API is not using $default; strip it so the
    # route table stays about the API's own shape.
    stage = ctx.get("stage")
    if stage and stage != "$default" and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1 :]
    path = path.rstrip("/") or "/"
    return method.upper(), path, identity


def handler(event: dict, context: Any = None) -> dict:
    """API Gateway entry point, accepting payload format 1.0 or 2.0."""
    global COLD_START
    started = time.perf_counter()

    method, path, identity = _normalise_request(event)

    request_id = bind_request(context, route=f"{method} {path}")
    set_route(f"{method} {path}")
    LOG.info(
        "request received",
        extra={
            "method": method,
            "path": path,
            "cold_start": COLD_START,
            "source_ip": identity.get("sourceIp"),
            "user_agent": identity.get("userAgent"),
            "query": event.get("queryStringParameters"),
            "body_bytes": len(event.get("body") or ""),
        },
    )

    if method == "OPTIONS":
        return respond(204, "")

    try:
        route_fn = ROUTES.get((method, path))
        if route_fn is None:
            raise ApiError(
                404,
                f"no route for {method} {path}",
                available=[f"{m} {p}" for m, p in sorted(ROUTES)],
            )
        response = route_fn(event)
        status = response["statusCode"]
    except ApiError as exc:
        LOG.warning(
            "request rejected",
            extra={"status": exc.status, "reason": exc.message, "detail": exc.detail},
        )
        response = respond(
            exc.status, {"error": exc.message, **exc.detail, "request_id": request_id}
        )
        status = exc.status
    except Exception as exc:  # noqa: BLE001 - the last line of defence
        LOG.error(
            "unhandled exception",
            extra={
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            exc_info=True,
        )
        response = respond(500, {"error": "internal error", "request_id": request_id})
        status = 500

    duration_ms = (time.perf_counter() - started) * 1000.0
    LOG.info(
        "request complete",
        extra={
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "cold_start": COLD_START,
            "response_bytes": len(response.get("body") or ""),
        },
    )
    COLD_START = False
    return response
