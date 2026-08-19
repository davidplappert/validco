"""Domain models.

The application's weight lives here: profiles derive their own physiology,
efforts compute themselves from a route's shape, health reports carry their own
caveats, and routes serialise themselves. Controllers stay thin because these
objects already know how to answer the questions asked of them.
"""

from .effort import WalkEffort, WalkSegment
from .health import EnergyReport, GuidelineProgress, HealthReport, JointLoadReport, StepProgress
from .location import Coordinate, GeocodeResult, Origin, compass, haversine
from .profile import Profile, Sex
from .region import BoundingBox, Region
from .route import (
    ElevationProfile,
    Leg,
    Route,
    RouteGeometry,
    SurfaceBreakdown,
    WalkSegmentBuilder,
)
from .suitability import SuitabilityAssessment, SuitabilityRule

__all__ = [
    "BoundingBox",
    "Coordinate",
    "ElevationProfile",
    "EnergyReport",
    "GeocodeResult",
    "GuidelineProgress",
    "HealthReport",
    "JointLoadReport",
    "Leg",
    "Origin",
    "Profile",
    "Region",
    "Route",
    "RouteGeometry",
    "Sex",
    "StepProgress",
    "SuitabilityAssessment",
    "SuitabilityRule",
    "SurfaceBreakdown",
    "WalkEffort",
    "WalkSegment",
    "WalkSegmentBuilder",
    "compass",
    "haversine",
]
