"""Shared catalogue of district-polygon topology cases.

This is a deliberately synthetic fixture catalogue of GeoJSON polygon
topology cases used to regression-test spatial validation: degenerate rings,
self-intersecting ("bow-tie") rings, holes that lie outside or cross their
shell, and multipolygon parts that overlap, touch, or sit disjoint from one
another. It is consumed both by the Python ingestion unit tests and by a
PostGIS cross-check (via the ``WKT_BY_NAME`` strings below), so every case
must carry a verdict that is true in GEOS, not merely asserted.

This module is test-support code, not production code. It intentionally has
no dependency on shapely (or anything else beyond the standard library) so
that it stays trivially importable from a non-Python (SQL) test suite as
well: it is pure data plus a couple of tiny lookup helpers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeometryCase:
    """One named GeoJSON geometry with its expected GEOS validity verdict."""

    name: str
    geometry: dict
    valid: bool
    reason: str


GEOMETRY_CASES: tuple[GeometryCase, ...] = (
    # -- invalid -----------------------------------------------------------
    GeometryCase(
        name="degenerate_point",
        geometry={
            "type": "Polygon",
            "coordinates": [[[6, 50], [6, 50], [6, 50], [6, 50]]],
        },
        valid=False,
        reason="too few points",
    ),
    GeometryCase(
        name="bowtie",
        geometry={
            "type": "Polygon",
            "coordinates": [[[6, 50], [7, 51], [6, 51], [7, 50], [6, 50]]],
        },
        valid=False,
        reason="self-intersection",
    ),
    GeometryCase(
        name="zero_area_sliver",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 1], [2, 2], [0, 0]]],
        },
        valid=False,
        reason="self-intersection",
    ),
    GeometryCase(
        name="hole_outside_shell",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
                [[2, 2], [2.5, 2], [2.5, 2.5], [2, 2.5], [2, 2]],
            ],
        },
        valid=False,
        reason="hole outside shell",
    ),
    GeometryCase(
        name="hole_crossing_shell",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
                [[0.5, 0.5], [1.5, 0.5], [1.5, 0.9], [0.5, 0.9], [0.5, 0.5]],
            ],
        },
        valid=False,
        reason="self-intersection",
    ),
    GeometryCase(
        name="overlapping_multipolygon",
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]],
            ],
        },
        valid=False,
        reason="self-intersection",
    ),
    # -- valid controls ------------------------------------------------------
    GeometryCase(
        name="valid_square",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        valid=True,
        reason="valid",
    ),
    GeometryCase(
        name="valid_with_hole",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
                [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]],
            ],
        },
        valid=True,
        reason="valid",
    ),
    GeometryCase(
        name="disjoint_multipolygon",
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]],
            ],
        },
        valid=True,
        reason="valid",
    ),
    # Two multipolygon parts sharing a full edge, not merely a point. Per
    # OGC simple-feature rules a MultiPolygon's elements may touch only at a
    # finite number of points, not along a line, so GEOS (verified against
    # Shapely 2.1.2 / GEOS 3.13.1) actually reports this as invalid, with a
    # self-intersection at the shared vertex (1, 1).
    GeometryCase(
        name="touching_multipolygon",
        geometry={
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]],
            ],
        },
        valid=False,
        reason="self-intersection",
    ),
)


def case(name: str) -> GeometryCase:
    """Look up a :class:`GeometryCase` by name, or raise ``KeyError(name)``."""
    for entry in GEOMETRY_CASES:
        if entry.name == name:
            return entry
    raise KeyError(name)


def invalid_cases() -> tuple[GeometryCase, ...]:
    """Every case expected to be GEOS-invalid."""
    return tuple(entry for entry in GEOMETRY_CASES if not entry.valid)


def valid_cases() -> tuple[GeometryCase, ...]:
    """Every case expected to be GEOS-valid."""
    return tuple(entry for entry in GEOMETRY_CASES if entry.valid)


# WKT form of every case above, hand-written (not shapely-generated) so this
# module stays dependency-free. Coordinates are formatted without trailing
# zeros (``6 50``, ``0.5 0.5``) to match the GeoJSON coordinates verbatim;
# the SQL suite feeds these into ``ST_GeomFromText`` to confirm PostGIS
# reaches the same validity verdict as GEOS via shapely.
WKT_BY_NAME: dict[str, str] = {
    "degenerate_point": "POLYGON((6 50,6 50,6 50,6 50))",
    "bowtie": "POLYGON((6 50,7 51,6 51,7 50,6 50))",
    "zero_area_sliver": "POLYGON((0 0,1 1,2 2,0 0))",
    "hole_outside_shell": (
        "POLYGON((0 0,1 0,1 1,0 1,0 0),(2 2,2.5 2,2.5 2.5,2 2.5,2 2))"
    ),
    "hole_crossing_shell": (
        "POLYGON((0 0,1 0,1 1,0 1,0 0),(0.5 0.5,1.5 0.5,1.5 0.9,0.5 0.9,0.5 0.5))"
    ),
    "overlapping_multipolygon": (
        "MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)),"
        "((0.5 0.5,1.5 0.5,1.5 1.5,0.5 1.5,0.5 0.5)))"
    ),
    "valid_square": "POLYGON((0 0,1 0,1 1,0 1,0 0))",
    "valid_with_hole": (
        "POLYGON((0 0,1 0,1 1,0 1,0 0),(0.4 0.4,0.6 0.4,0.6 0.6,0.4 0.6,0.4 0.4))"
    ),
    "disjoint_multipolygon": (
        "MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)),((2 0,3 0,3 1,2 1,2 0)))"
    ),
    "touching_multipolygon": (
        "MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)),((1 0,2 0,2 1,1 1,1 0)))"
    ),
}
