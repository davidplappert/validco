"""Static configuration for the StepWise dataset build.

Everything the build depends on is pinned here so a rebuild is reproducible:
the Overture release, the geographic window, and the classification tables that
turn raw Overture attributes into the walk-quality model the API reasons about.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import NamedTuple

# The surface/flag encodings are the *wire format* of the baked artifacts, so
# they are defined once alongside the runtime reader and imported here. Keeping
# a second copy in the builder is exactly how a dataset ends up being written
# with one meaning and read with another.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
from stepwise.config import (  # noqa: E402
    DATASET_VERSION,
    FLAG_BRIDGE,
    FLAG_BUSY,
    FLAG_INDOOR,
    FLAG_STEPS,
    FLAG_TUNNEL,
    FLAG_UNPAVED,
    SURFACE_COST,
    SURFACE_CROSSING,
    SURFACE_PATH,
    SURFACE_ROAD,
    SURFACE_SIDEWALK,
    SURFACES,
)

__all__ = [
    "DATASET_VERSION",
    "FLAG_BRIDGE",
    "FLAG_BUSY",
    "FLAG_INDOOR",
    "FLAG_STEPS",
    "FLAG_TUNNEL",
    "FLAG_UNPAVED",
    "SURFACE_COST",
    "SURFACE_CROSSING",
    "SURFACE_PATH",
    "SURFACE_ROAD",
    "SURFACE_SIDEWALK",
    "SURFACES",
]

LOG = logging.getLogger(__name__)

# --- Overture source -------------------------------------------------------

OVERTURE_RELEASE = "2026-07-22.0"
OVERTURE_S3 = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"
OVERTURE_REGION = "us-west-2"


class Region(NamedTuple):
    """One buildable coverage area.

    Each region bakes into its own set of artifacts, so adding a city is a
    matter of appending an entry here and re-running the build — nothing in the
    routing or health code is city-specific.
    """

    key: str
    label: str
    bbox: tuple[float, float, float, float]  # west, south, east, north
    center: tuple[float, float]  # lat, lon — where the map opens


REGIONS: dict[str, Region] = {
    # San Francisco: the city Valid's own demo uses, and a genuinely hard case —
    # 43k places, extreme terrain, and near-complete sidewalk coverage.
    "sf": Region(
        key="sf",
        label="San Francisco, CA",
        bbox=(-122.5300, 37.6900, -122.3400, 37.8400),
        center=(37.7749, -122.4194),
    ),
    # Peoria / Chillicothe, IL: a small-city counterpoint with sparser sidewalk
    # coverage and gentler bluff terrain. Proves the pipeline is not tuned to
    # one atypical city.
    "pia": Region(
        key="pia",
        label="Peoria & Chillicothe, IL",
        bbox=(-89.8000, 40.6000, -89.3000, 41.0000),
        center=(40.6936, -89.5890),
    ),
}

DEFAULT_REGION = "sf"

# --- Elevation -------------------------------------------------------------

# AWS Open Data "Terrain Tiles" — global DEM as Terrarium-encoded PNG, free and
# unauthenticated. Source under SF is USGS 3DEP 1/3 arc-second (~10 m), so z=14
# (~9.5 m/px at this latitude) is the honest native resolution; sampling finer
# would invent detail the DEM does not have.
TERRAIN_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRAIN_ZOOM = 14

# --- Walk network classification ------------------------------------------

# Overture `class` values we consider walkable. Motorway/trunk are excluded
# outright: they are freeway-grade and have no legal pedestrian access in SF.
WALKABLE_CLASSES = {
    "footway",
    "path",
    "pedestrian",
    "steps",
    "track",
    "cycleway",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
}

# Road classes that carry enough traffic to be unpleasant on foot even where a
# sidewalk exists; used to nudge routes onto quieter parallel streets.
BUSY_ROAD_CLASSES = {"primary", "secondary", "tertiary"}

UNPAVED_SURFACES = {"dirt", "gravel", "ground", "unpaved", "grass", "sand", "wood", "compacted"}

# --- Destinations ----------------------------------------------------------

# Overture place categories that make a walk worth taking. Grouped so the API
# can explain *why* it picked a destination.
DESTINATION_CATEGORIES = {
    "green": {
        "park",
        "garden",
        "botanical_garden",
        "dog_park",
        "playground",
        "state_park",
        "national_park",
        "nature_preserve",
        "forest",
    },
    "scenic": {
        "beach",
        "landmark_and_historical_building",
        "monument",
        "scenic_spot",
        "tourist_attraction",
        "pier",
        "lookout",
        "observation_deck",
        "plaza",
    },
    "culture": {
        "art_gallery",
        "museum",
        "library",
        "public_art",
        "history_museum",
        "art_museum",
        "performing_arts",
    },
    "refuel": {
        "coffee_shop",
        "cafe",
        "bakery",
        "juice_bar_and_smoothies",
        "ice_cream_shop",
        "tea_room",
    },
    "active": {
        "gym",
        "sports_club_and_league",
        "recreation_center",
        "trail",
        "stadium_arena",
        "swimming_pool",
    },
}

# Green land-use polygons drive the "how much of this walk is next to green
# space" score. Overture base/land_use subtypes.
GREEN_LANDUSE_SUBTYPES = {"park", "recreation", "horticulture", "forest", "conservation"}

# Distance (metres) from an edge to a green polygon centroid for that edge to
# count as "green-adjacent". Deliberately generous — city parks are large and
# the point is "does this walk feel green", not a precise buffer.
GREEN_PROXIMITY_M = 120.0
