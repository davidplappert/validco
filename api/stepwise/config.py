"""Constants shared by the offline builder and the runtime API.

These describe the *wire format* of the baked artifacts — which integer means
"sidewalk", which bit means "stairs". They live with the runtime code and are
imported by the build pipeline (not the other way round), so a change here can
never leave a freshly-built dataset being read with stale semantics.
"""

from __future__ import annotations

# Surface classes, stored per edge as a uint8 index. Order is the wire format.
SURFACES = ("path", "sidewalk", "crossing", "road")
SURFACE_PATH, SURFACE_SIDEWALK, SURFACE_CROSSING, SURFACE_ROAD = range(4)

# Human-facing labels for the four surfaces — this is the "walk on a road or a
# walking path" distinction the product is built around.
SURFACE_LABELS = {
    SURFACE_PATH: "Walking path",
    SURFACE_SIDEWALK: "Sidewalk",
    SURFACE_CROSSING: "Street crossing",
    SURFACE_ROAD: "Road (no sidewalk mapped)",
}

# Routing cost multipliers by surface. 1.0 is neutral; >1 means the router will
# accept extra distance to avoid it. These are comfort preferences — distance
# and gradient are modelled separately as physical cost.
SURFACE_COST = {
    SURFACE_PATH: 0.85,  # dedicated walking infrastructure: prefer it
    SURFACE_SIDEWALK: 1.00,  # the baseline urban walk
    SURFACE_CROSSING: 1.35,  # waiting at lights, exposure to traffic
    SURFACE_ROAD: 1.60,  # walking in or along the roadway itself
}

# Per-edge bit flags, stored as a uint8 mask.
FLAG_STEPS = 1 << 0  # stairs — impassable with wheels or limited mobility
FLAG_BUSY = 1 << 1  # alongside a busy arterial
FLAG_UNPAVED = 1 << 2  # dirt, gravel, ground
FLAG_BRIDGE = 1 << 3
FLAG_TUNNEL = 1 << 4
FLAG_INDOOR = 1 << 5

FLAG_LABELS = {
    FLAG_STEPS: "stairs",
    FLAG_BUSY: "busy road",
    FLAG_UNPAVED: "unpaved",
    FLAG_BRIDGE: "bridge",
    FLAG_TUNNEL: "tunnel",
    FLAG_INDOOR: "covered",
}

# Bumped whenever the artifact layout changes in a way the reader must know
# about. v2 added `edge_grade_dpct`, the precomputed per-edge gradient.
DATASET_VERSION = 2

# Gradients are stored per edge as decipercent (rise/run * 1000) in an int16,
# clamped to the range Minetti's curve was fitted over. This is also the index
# space for the routing lookup tables, so the two definitions must agree.
#
# 0.1% resolution is finer than the ~10 m DEM the gradients came from, so the
# quantisation introduces no error the underlying data did not already have.
GRADE_SCALE = 1000.0
GRADE_LIMIT_DPCT = 450
GRADE_TABLE_SIZE = GRADE_LIMIT_DPCT * 2 + 1
