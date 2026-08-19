"""Tests that the OpenAPI document stays true.

A specification that drifts from the implementation is worse than none: it tells
a consumer something false with authority. These tests are cheap insurance
against exactly that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"


@pytest.fixture(scope="module")
def spec() -> dict:
    """The parsed OpenAPI document."""
    return yaml.safe_load(SPEC_PATH.read_text())


def test_is_a_valid_openapi_document(spec):
    """Structural validation against the OpenAPI 3.1 meta-schema."""
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(spec)


def test_documents_exactly_the_routes_the_router_serves(spec):
    """The spec and the route table must not drift apart.

    Adding an endpoint without documenting it, or documenting one that was
    removed, both fail here.
    """
    from stepwise.handler import build_router

    documented = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    implemented = {tuple(route.split(" ", 1)) for route in build_router().routes()}
    assert documented == implemented


def test_every_operation_has_an_id_and_a_summary(spec):
    """Operation ids drive generated clients; summaries drive readable docs."""
    seen: set[str] = set()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation.get("operationId"), f"{method} {path} has no operationId"
            assert operation.get("summary"), f"{method} {path} has no summary"
            assert operation["operationId"] not in seen, "operationId must be unique"
            seen.add(operation["operationId"])


def test_region_keys_match_the_shipped_datasets(spec):
    """The documented enum must be the regions actually built."""
    from stepwise.datasets.registry import REGISTRY

    documented = set(spec["components"]["schemas"]["RegionKey"]["enum"])
    assert documented == set(REGISTRY.keys())


def test_documents_the_error_statuses_the_api_can_return(spec):
    """422 is the interesting one: it is how the app states the limits of its
    data rather than blaming the caller, and it must be documented."""
    plan_responses = set(spec["paths"]["/v1/plan"]["post"]["responses"])
    assert {"200", "400", "404", "422"} <= plan_responses


def test_health_thresholds_match_the_models(spec):
    """The published guideline constants are load-bearing claims; if the model
    changes one, the document must not keep asserting the old number."""
    from stepwise.models.health import GuidelineProgress, StepProgress

    health = spec["components"]["schemas"]["Health"]["properties"]
    guideline = health["guideline_progress"]["properties"]
    assert guideline["who_weekly_moderate_min"]["const"] == GuidelineProgress.WEEKLY_TARGET_MIN
    assert guideline["who_weekly_upper_min"]["const"] == GuidelineProgress.WEEKLY_UPPER_MIN
    assert health["steps"]["properties"]["daily_target"]["const"] == StepProgress.DAILY_TARGET


def test_surface_enum_matches_the_wire_format(spec):
    """Surface codes are a wire format shared with the offline builder."""
    from stepwise.config import SURFACES

    documented = spec["components"]["schemas"]["RouteGeometry"]["properties"]["segments"]
    enum = documented["items"]["properties"]["surface"]["enum"]
    assert set(enum) == set(SURFACES)


def test_feature_flags_match_the_wire_format(spec):
    from stepwise.config import FLAG_LABELS

    route = spec["components"]["schemas"]["Route"]["properties"]
    assert set(route["features"]["items"]["enum"]) == set(FLAG_LABELS.values())
