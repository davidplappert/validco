"""The catalogue of coverage areas, and the store they are built into.

This is what turns StepWise from a two-city demo into a universal app: any city
in Overture can be requested, is extracted once, and is then served from cache
for everyone.

Why S3 alone, and no database
-----------------------------
The obvious design is a DynamoDB table for state plus S3 for artifacts. This
uses S3 for both, because S3 gained conditional writes (``If-None-Match``) in
late 2024, and that is the only genuinely hard requirement here: two people
asking for Austin at the same second must produce **one** build, not two. A
conditional ``PutObject`` gives that atomicity directly.

What is left is a status document and a set of artifacts per region, both of
which S3 stores well. Dropping DynamoDB removes a service, a set of IAM
permissions, and a second consistency model, in exchange for nothing — status
polling is a few GETs per build.

Note this does **not** contradict the "no database for routing" argument. The
graph is still held as flat arrays in memory, because a Dijkstra that settles
tens of thousands of nodes cannot make network calls. S3 here is an *artifact*
store consulted a handful of times per cold start, not a query engine.

Layout::

    s3://<bucket>/regions/<key>/status.json     state, progress, bbox
    s3://<bucket>/regions/<key>/<key>.graph.spw
    s3://<bucket>/regions/<key>/<key>.addr.spw
    s3://<bucket>/regions/<key>/<key>.places.spw
    s3://<bucket>/regions/<key>/<key>.green.spw
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from typing import Any

LOG = logging.getLogger(__name__)

#: Lifecycle of a coverage area.
PENDING = "pending"
BUILDING = "building"
READY = "ready"
FAILED = "failed"

#: A build that has not reported progress in this long is presumed dead — the
#: Lambda was killed, or timed out — and may be reclaimed. Slightly longer than
#: the builder's own timeout so a slow build is never stolen from itself.
STALE_AFTER_S = 20 * 60

#: How large an area may be requested, in square degrees. A whole country would
#: take hours to extract and would not fit in a Lambda's memory; a metro area is
#: the unit this product actually works in.
MAX_AREA_SQ_DEG = 1.2

#: Minimum window, so a request for a village still yields a walkable network.
MIN_SPAN_DEG = 0.04


class RegionKey:
    """Derives a stable, filesystem- and URL-safe key for a coverage area."""

    _SAFE = re.compile(r"[^a-z0-9]+")

    @classmethod
    def from_name(cls, name: str, country: str = "", region: str = "") -> str:
        """Slugify a place name, qualified by country and state.

        Qualification matters: there are Austins in Texas, Minnesota and
        Michigan, and they must not collide into one cache entry.
        """
        parts = [p for p in (country, region, name) if p]
        slug = cls._SAFE.sub("-", cls._ascii(" ".join(parts)).lower()).strip("-")
        return slug[:64] or "unknown"

    @staticmethod
    def _ascii(value: str) -> str:
        """Fold accents so `Zürich` and `Zurich` produce the same key."""
        decomposed = unicodedata.normalize("NFKD", value)
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class RegionStatus:
    """The state of one coverage area, as stored in ``status.json``."""

    __slots__ = (
        "key",
        "label",
        "state",
        "progress",
        "stage",
        "message",
        "bbox",
        "center",
        "updated_at",
        "stats",
        "error",
    )

    def __init__(self, **fields: Any):
        """Build from a status document, defaulting anything absent."""
        self.key: str = fields["key"]
        self.label: str = fields.get("label", fields["key"])
        self.state: str = fields.get("state", PENDING)
        self.progress: float = float(fields.get("progress", 0.0))
        self.stage: str = fields.get("stage", "")
        self.message: str = fields.get("message", "")
        self.bbox: list[float] | None = fields.get("bbox")
        self.center: list[float] | None = fields.get("center")
        self.updated_at: float = float(fields.get("updated_at", 0.0))
        self.stats: dict[str, Any] = fields.get("stats") or {}
        self.error: str = fields.get("error", "")

    @property
    def is_ready(self) -> bool:
        """Whether this region can serve plan requests."""
        return self.state == READY

    @property
    def is_stale(self) -> bool:
        """Whether an in-flight build appears to have died.

        A Lambda that is killed mid-build leaves the status at ``building``
        forever. Without this, one crashed build would poison a city
        permanently.
        """
        if self.state != BUILDING:
            return False
        return (time.time() - self.updated_at) > STALE_AFTER_S

    def to_dict(self) -> dict[str, Any]:
        """Serialise, for storage and for the API response."""
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "progress": round(self.progress, 3),
            "stage": self.stage,
            "message": self.message,
            "bbox": self.bbox,
            "center": self.center,
            "updated_at": self.updated_at,
            "stats": self.stats,
            **({"error": self.error} if self.error else {}),
        }


class RegionCatalog:
    """Reads and writes region state in S3.

    ``boto3`` is imported lazily and the client built on first use, so an
    invocation that only touches bundled regions never pays for it — which is
    the common case for the two showcase cities.
    """

    def __init__(self, bucket: str | None = None, prefix: str = "regions"):
        """Bind a bucket, defaulting to the one the stack injected."""
        self.bucket = bucket if bucket is not None else os.environ.get("REGION_BUCKET", "")
        self.prefix = prefix
        self._client = None

    @property
    def enabled(self) -> bool:
        """Whether on-demand regions are configured at all.

        False in local development and in tests, where the bundled regions are
        the whole world and no bucket exists.
        """
        return bool(self.bucket)

    @property
    def client(self):
        """The S3 client, created on first use."""
        if self._client is None:
            import boto3  # noqa: PLC0415 — deliberately lazy

            self._client = boto3.client("s3")
        return self._client

    def _status_key(self, key: str) -> str:
        return f"{self.prefix}/{key}/status.json"

    def artifact_key(self, key: str, suffix: str) -> str:
        """S3 key for one of a region's baked containers."""
        return f"{self.prefix}/{key}/{key}.{suffix}.spw"

    # --- reading ----------------------------------------------------------

    def get(self, key: str) -> RegionStatus | None:
        """Fetch one region's status, or ``None`` if it has never been requested."""
        if not self.enabled:
            return None
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._status_key(key))
        except Exception as exc:  # noqa: BLE001 — NoSuchKey and friends
            if "NoSuchKey" in type(exc).__name__ or "404" in str(exc):
                return None
            LOG.warning("catalog get failed key=%s err=%s", key, exc)
            return None
        return RegionStatus(**json.loads(body["Body"].read()))

    def list(self) -> list[RegionStatus]:
        """Every region that has been requested, in any state."""
        if not self.enabled:
            return []
        statuses: list[RegionStatus] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{self.prefix}/"):
            for item in page.get("Contents", []):
                if item["Key"].endswith("/status.json"):
                    key = item["Key"].split("/")[-2]
                    status = self.get(key)
                    if status:
                        statuses.append(status)
        return statuses

    # --- writing ----------------------------------------------------------

    def claim(self, status: RegionStatus) -> bool:
        """Atomically take ownership of building a region.

        Uses a conditional ``PutObject`` so that concurrent requests for the
        same city produce exactly one build. The loser of the race gets
        ``False`` and simply reports the winner's progress — which is what the
        user wants to see anyway.
        """
        if not self.enabled:
            return False
        status.state = BUILDING
        status.updated_at = time.time()
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._status_key(status.key),
                Body=json.dumps(status.to_dict()).encode(),
                ContentType="application/json",
                IfNoneMatch="*",
            )
            LOG.info("claimed region build key=%s", status.key)
            return True
        except Exception as exc:  # noqa: BLE001 — PreconditionFailed means someone won
            LOG.info("region already claimed key=%s (%s)", status.key, type(exc).__name__)
            return False

    def put(self, status: RegionStatus) -> None:
        """Overwrite a region's status unconditionally.

        Used for progress updates and for reclaiming a stale build, both of
        which are deliberately not conditional: the writer already knows it owns
        the build.
        """
        if not self.enabled:
            return
        status.updated_at = time.time()
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._status_key(status.key),
            Body=json.dumps(status.to_dict()).encode(),
            ContentType="application/json",
            CacheControl="no-cache",
        )

    def progress(
        self, status: RegionStatus, fraction: float, stage: str, message: str = ""
    ) -> None:
        """Record build progress, for the client's progress bar."""
        status.progress = max(0.0, min(1.0, fraction))
        status.stage = stage
        status.message = message
        self.put(status)
        LOG.info(
            "region build progress key=%s pct=%.0f stage=%s",
            status.key,
            status.progress * 100,
            stage,
        )

    def fail(self, status: RegionStatus, error: str) -> None:
        """Mark a build failed, keeping the reason for the user."""
        status.state = FAILED
        status.error = error[:500]
        self.put(status)
        LOG.error("region build failed key=%s error=%s", status.key, error)

    def complete(self, status: RegionStatus, stats: dict[str, Any]) -> None:
        """Mark a build finished and record what it produced."""
        status.state = READY
        status.progress = 1.0
        status.stage = "ready"
        status.message = ""
        status.stats = stats
        self.put(status)
        LOG.info("region build complete key=%s stats=%s", status.key, stats)

    def download(self, key: str, suffix: str, destination: str) -> bool:
        """Fetch one baked container to local disk. Returns whether it existed."""
        if not self.enabled:
            return False
        try:
            self.client.download_file(self.bucket, self.artifact_key(key, suffix), destination)
            return True
        except Exception as exc:  # noqa: BLE001
            LOG.warning("artifact download failed key=%s suffix=%s err=%s", key, suffix, exc)
            return False

    def upload(self, key: str, suffix: str, source: str) -> None:
        """Store one baked container."""
        self.client.upload_file(
            source,
            self.bucket,
            self.artifact_key(key, suffix),
            ExtraArgs={"ContentType": "application/octet-stream"},
        )


def clamp_bbox(bbox: list[float]) -> list[float]:
    """Confine a requested window to something buildable.

    Grows a too-small box around its centre so a village still gets a walkable
    network, and shrinks a too-large one so nobody can ask for a country and
    time out the builder. Both bounds are about the unit this product works in:
    a place you could walk across a corner of.
    """
    west, south, east, north = bbox
    cx, cy = (west + east) / 2.0, (south + north) / 2.0

    span_x = max(east - west, MIN_SPAN_DEG)
    span_y = max(north - south, MIN_SPAN_DEG)

    area = span_x * span_y
    if area > MAX_AREA_SQ_DEG:
        scale = (MAX_AREA_SQ_DEG / area) ** 0.5
        span_x *= scale
        span_y *= scale

    return [cx - span_x / 2, cy - span_y / 2, cx + span_x / 2, cy + span_y / 2]
