"""Shared fixtures.

The dataset fixtures are module-scoped: loading the San Francisco graph decodes
several megabytes of arrays, and doing that per test would dominate the run.
"""

from __future__ import annotations

import pytest
from stepwise.datasets.registry import REGISTRY
from stepwise.models.profile import Profile
from stepwise.physiology.anthropometry import UnitConverter


@pytest.fixture(scope="session")
def registry():
    """The process-wide dataset registry, pointed at the shipped artifacts."""
    return REGISTRY


@pytest.fixture(scope="session")
def sf(registry):
    """San Francisco datasets — dense sidewalks, extreme terrain."""
    return registry.datasets("sf")


@pytest.fixture(scope="session")
def pia(registry):
    """Peoria/Chillicothe datasets — sparse sidewalks, gentle bluffs."""
    return registry.datasets("pia")


@pytest.fixture
def heavy_profile() -> Profile:
    """A class III obesity profile — the user this product is built for."""
    return Profile("male", 33, UnitConverter.lb_to_kg(361), 182.9)


@pytest.fixture
def lean_profile() -> Profile:
    """A healthy-BMI profile, for comparison against the above."""
    return Profile("male", 33, 75.0, 178.0)


@pytest.fixture
def older_profile() -> Profile:
    """An older walker, where the gait-speed norms differ materially."""
    return Profile("female", 72, 62.0, 160.0)
