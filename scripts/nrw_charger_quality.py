from __future__ import annotations

import copy
import math
from collections.abc import Iterable

from shapely.geometry import shape
from shapely.validation import explain_validity


EXCEPTION_FIELDS = (
    "id",
    "name",
    "operator",
    "district_text",
    "longitude",
    "latitude",
    "reason",
)
ALLOWED_REASONS = frozenset({"invalid_coordinates", "outside_nrw", "duplicate_id"})


def _validate_ring(ring: object, *, district: str, role: str) -> list[list[float]]:
    """Reject ring structure that is not even parseable as a polygon ring.

    This is a precondition for the topology check in :func:`_validate_geometry`,
    not a substitute for it: an unclosed or non-finite ring cannot be handed to
    a geometry engine for a meaningful verdict.
    """
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError(f"District {district} has a {role} ring with fewer than four positions")
    for position in ring:
        if (
            not isinstance(position, list)
            or len(position) < 2
            or isinstance(position[0], bool)
            or isinstance(position[1], bool)
            or not all(isinstance(value, (int, float)) for value in position[:2])
            or not all(math.isfinite(float(value)) for value in position[:2])
        ):
            raise ValueError(f"District {district} has a non-finite {role} ring position")
    if [float(value) for value in ring[0][:2]] != [float(value) for value in ring[-1][:2]]:
        raise ValueError(f"District {district} has an unclosed {role} ring")
    return ring


def _validate_geometry(geometry: object, *, district: str) -> dict:
    if not isinstance(geometry, dict):
        raise ValueError(f"District {district} has no geometry")
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        polygons = [geometry.get("coordinates")]
    elif geometry_type == "MultiPolygon":
        polygons = list(geometry.get("coordinates") or [])
        if not polygons:
            raise ValueError(f"District {district} has an empty MultiPolygon")
    else:
        raise ValueError(f"Unsupported district geometry: {geometry_type}")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError(f"District {district} has a polygon without an exterior ring")
        _validate_ring(polygon[0], district=district, role="exterior")
        for hole in polygon[1:]:
            _validate_ring(hole, district=district, role="interior")

    # Structural checks above cannot see topology: a ring of four identical
    # positions, a self-crossing "bow-tie" outline, a hole outside its shell and
    # overlapping MultiPolygon parts are all well-formed lists and all invalid
    # polygons.  Containment against such a shape is undefined, so the verdict
    # comes from GEOS, the same engine PostGIS uses, rather than from another
    # hand-written topology checker.  Invalid input is reported with the
    # district and the engine's own reason; boundaries are never repaired,
    # because silently rewriting a district outline would move the very
    # boundary the assignment depends on.
    outline = shape(geometry)
    if outline.is_empty:
        raise ValueError(f"District {district} has empty geometry")
    if not outline.is_valid:
        raise ValueError(
            f"District {district} has invalid geometry: {explain_validity(outline)}"
        )
    return geometry


def _point_on_segment(
    point: tuple[float, float],
    start: list[float],
    end: list[float],
    tolerance: float = 1e-12,
) -> bool:
    x, y = point
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > tolerance:
        return False
    return (
        min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    )


def _ring_covers(point: tuple[float, float], ring: list[list[float]]) -> tuple[bool, bool]:
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if _point_on_segment(point, previous, current):
            return True, True
        x, y = point
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return inside, False


def _polygon_covers(point: tuple[float, float], polygon: list[list[list[float]]]) -> bool:
    outer_inside, outer_boundary = _ring_covers(point, polygon[0])
    if not outer_inside:
        return False
    if outer_boundary:
        return True
    for hole in polygon[1:]:
        hole_inside, hole_boundary = _ring_covers(point, hole)
        if hole_inside and not hole_boundary:
            return False
        if hole_boundary:
            return True
    return True


def _geometry_covers(point: tuple[float, float], geometry: dict) -> bool:
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        return _polygon_covers(point, geometry["coordinates"])
    if geometry_type == "MultiPolygon":
        return any(_polygon_covers(point, polygon) for polygon in geometry["coordinates"])
    raise ValueError(f"Unsupported district geometry: {geometry_type}")


def _collect_coordinates(values: object) -> Iterable[tuple[float, float]]:
    if (
        isinstance(values, list)
        and len(values) >= 2
        and isinstance(values[0], (int, float))
        and isinstance(values[1], (int, float))
    ):
        yield float(values[0]), float(values[1])
    elif isinstance(values, list):
        for value in values:
            yield from _collect_coordinates(value)


def _region_bbox(region: dict) -> tuple[float, float, float, float]:
    coordinates = list(_collect_coordinates(region["geometry"]["coordinates"]))
    if not coordinates:
        raise ValueError("District geometry has no coordinates")
    xs = [coordinate[0] for coordinate in coordinates]
    ys = [coordinate[1] for coordinate in coordinates]
    return min(xs), min(ys), max(xs), max(ys)


def _valid_coordinates(coordinates: object) -> tuple[float, float] | None:
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    if isinstance(coordinates[0], bool) or isinstance(coordinates[1], bool):
        return None
    try:
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _exception(candidate: dict, reason: str) -> dict:
    properties = candidate.get("properties") or {}
    geometry = candidate.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    longitude = coordinates[0] if isinstance(coordinates, list) and coordinates else None
    latitude = coordinates[1] if isinstance(coordinates, list) and len(coordinates) > 1 else None
    values = {
        "id": properties.get("id"),
        "name": properties.get("name"),
        "operator": properties.get("operator"),
        "district_text": properties.get("district_text"),
        "longitude": longitude,
        "latitude": latitude,
        "reason": reason,
    }
    return {field: values[field] for field in EXCEPTION_FIELDS}


def classify_chargers(candidates: list[dict], regions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Assign each source station to exactly one NRW district.

    Containment follows the database's ``ST_Covers`` semantics: a point in a
    polygon hole is outside, and a point on an exterior or hole boundary is
    inside.  A point on a shared district boundary is covered by both
    neighbours, so it is resolved deterministically to the lowest NUTS code,
    the same rule as ``staging.nrw_charger_districts``.
    """
    for region in regions:
        _validate_geometry(
            region.get("geometry"), district=str(region["properties"].get("nuts_code"))
        )
    region_index = [(region, _region_bbox(region)) for region in regions]
    seen_ids: set[str] = set()
    accepted: list[dict] = []
    rejected: list[dict] = []

    for candidate in candidates:
        properties = candidate.get("properties") or {}
        raw_id = properties.get("id")
        if raw_id is None or not str(raw_id).strip():
            raise ValueError("Charging record is missing id")
        station_id = str(raw_id)
        if station_id in seen_ids:
            rejected.append(_exception(candidate, "duplicate_id"))
            continue
        seen_ids.add(station_id)

        point = _valid_coordinates((candidate.get("geometry") or {}).get("coordinates"))
        if point is None:
            rejected.append(_exception(candidate, "invalid_coordinates"))
            continue

        longitude, latitude = point
        matches = []
        for region, bbox in region_index:
            west, south, east, north = bbox
            if not (west <= longitude <= east and south <= latitude <= north):
                continue
            if _geometry_covers(point, region["geometry"]):
                matches.append(region)
        if not matches:
            rejected.append(_exception(candidate, "outside_nrw"))
            continue

        accepted_candidate = copy.deepcopy(candidate)
        region_properties = min(
            (match["properties"] for match in matches),
            key=lambda properties: str(properties["nuts_code"]),
        )
        accepted_candidate["properties"]["id"] = station_id
        accepted_candidate["properties"]["nuts_code"] = region_properties["nuts_code"]
        accepted_candidate["properties"]["district_name"] = region_properties["district_name"]
        accepted.append(accepted_candidate)

    return accepted, rejected


def assert_reconciled(
    source_count: int,
    accepted: list[dict],
    rejected: list[dict],
    region_codes: set[str],
) -> None:
    if len(accepted) + len(rejected) != source_count:
        raise ValueError("Charger reconciliation failed: accepted plus rejected does not equal source count")

    accepted_ids = [str(item["properties"].get("id")) for item in accepted]
    if len(accepted_ids) != len(set(accepted_ids)):
        raise ValueError("Accepted charger ids are not unique")

    for item in accepted:
        nuts_code = item["properties"].get("nuts_code")
        if nuts_code not in region_codes:
            raise ValueError(f"Accepted charger uses unknown NRW district: {nuts_code}")

    for item in rejected:
        if tuple(item) != EXCEPTION_FIELDS:
            raise ValueError("Charger exception fields do not match the approved schema")
        reason = item.get("reason")
        if reason not in ALLOWED_REASONS:
            raise ValueError(f"Unknown charger exception reason: {reason}")
