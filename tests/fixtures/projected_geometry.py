"""Independent EPSG:25832 geometry oracle for spatial regression tests.

This module deliberately re-derives distances using pyproj instead of
restating any SQL/PostGIS expressions, so it can check query results without
sharing assumptions with the code under test. It is test-support code only
and must never be imported from production code (``scripts/`` or ``db/``).
"""

from __future__ import annotations

from pyproj import Transformer

_TO_25832 = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)

LonLat = tuple[float, float]

# Canonical F11 counterexample, shared by the unit and SQL regression suites.
# The origin sits near a NRW district centroid.  At this latitude a degree of
# longitude is about 69.3 km while a degree of latitude is about 111.2 km, so
# "north" is nearer in raw lon/lat degrees while "east" is nearer in metres.
# ``ORDER BY ST_Centroid(d.geom) <-> feature.geom`` on EPSG:4326 geometry picks
# "north" and then reports a metre distance measured to it, which is the wrong
# feature by roughly 700 m.
DEGREE_ORDER_COUNTEREXAMPLE: dict[str, object] = {
    "origin": (7.6, 51.5),
    "candidates": {
        "north": (7.6, 51.525),
        "east": (7.63, 51.5),
    },
    "degree_nearest": "north",
    "metre_nearest": "east",
}


def project_25832(lon: float, lat: float) -> tuple[float, float]:
    """Project WGS84 lon/lat degrees to EPSG:25832 easting/northing metres."""
    easting, northing = _TO_25832.transform(lon, lat)
    return easting, northing


def projected_distance_m(a: LonLat, b: LonLat) -> float:
    """Planar distance in metres between two lon/lat points, in EPSG:25832."""
    ax, ay = project_25832(*a)
    bx, by = project_25832(*b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def projected_point_to_segment_distance_m(
    point: LonLat, seg_start: LonLat, seg_end: LonLat
) -> float:
    """Metre distance from a lon/lat point to a lon/lat segment, in EPSG:25832."""
    px, py = project_25832(*point)
    ax, ay = project_25832(*seg_start)
    bx, by = project_25832(*seg_end)

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_x, nearest_y = ax + t * dx, ay + t * dy
    return ((px - nearest_x) ** 2 + (py - nearest_y) ** 2) ** 0.5


def degree_distance(a: LonLat, b: LonLat) -> float:
    """Naive planar distance in raw lon/lat degrees (no projection).

    Models what the buggy ``ST_Centroid(d.geom) <-> feature.geom`` KNN
    operator orders candidates by: it treats a degree of longitude and a
    degree of latitude as equivalent units, which they are not.
    """
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def nearest_by_metres(
    origin: LonLat, candidates: dict[str, LonLat]
) -> tuple[str, float]:
    """Return (id, distance_m) of the candidate nearest to origin, by metres."""
    ranked = sorted(
        candidates.items(),
        key=lambda item: (projected_distance_m(origin, item[1]), item[0]),
    )
    winner_id, winner_point = ranked[0]
    return winner_id, projected_distance_m(origin, winner_point)


def nearest_by_degrees(
    origin: LonLat, candidates: dict[str, LonLat]
) -> tuple[str, float]:
    """Return (id, distance_deg) of the candidate nearest to origin, by degrees."""
    ranked = sorted(
        candidates.items(),
        key=lambda item: (degree_distance(origin, item[1]), item[0]),
    )
    winner_id, winner_point = ranked[0]
    return winner_id, degree_distance(origin, winner_point)
