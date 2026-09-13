"""Parse the raw NRW districts and BNetzA charging-register inputs.

This module deliberately has no dashboard export or scoring entry point.
PostGIS is the authoritative analytical model; canonical browser snapshots are
exported from its published views by ``scripts.export_nrw_runtime``.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import date
from pathlib import Path

from config_utils import ROOT
from source_cache import bnetza_csv_encoding
# The BNetzA register states its own publication date in the preamble above the
# header row, for example "Letzte Aktualisierung vom: 22.04.2026".  Nothing else
# in the download carries it, so it is read here and travels with the generated
# snapshot into raw.source_snapshots (manifest 9.3).
CHARGER_PREAMBLE_ROWS = 10
CHARGER_SNAPSHOT_PATTERN = re.compile(
    r"Letzte\s+Aktualisierung\s+vom:\s*(\d{2})\.(\d{2})\.(\d{4})",
    re.IGNORECASE,
)


def parse_charger_snapshot_date(preamble: str) -> str | None:
    """Return the register's publication date as ISO-8601, or None if absent.

    An unreadable or missing preamble date is an unknown snapshot date, never a
    substitute such as the download time or today.
    """
    match = CHARGER_SNAPSHOT_PATTERN.search(preamble)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_decimal(value: str) -> float | None:
    try:
        parsed = float(value.replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None
    return parsed if math.isfinite(parsed) else None


def max_point_power_kw(row: dict[str, str]) -> float | None:
    """Return the measured maximum valid BNetzA connector nominal power.

    Aggregate station power is intentionally not a fallback: a missing
    connector reading remains an unknown maximum-point-power classification.
    """
    values = [
        parse_decimal(row.get(f"Nennleistung Stecker{index}", row.get(f"Nennleistung Stecker{index} [kW]", "")))
        for index in range(1, 7)
    ]
    valid_values = [value for value in values if value is not None and value > 0]
    return max(valid_values, default=None)


def classify_power(maximum_kw: float | None) -> str | None:
    if maximum_kw is None:
        return None
    return "fast" if maximum_kw >= 50 else "normal"


def is_fast_power(maximum_kw: float | None) -> bool:
    return classify_power(maximum_kw) == "fast"


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_polygon(point: tuple[float, float], geometry: dict) -> bool:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return any(point_in_ring(point, polygon[0]) for polygon in polygons)


def load_nrw_regions(config: dict[str, object]) -> list[dict]:
    nuts = json.loads((ROOT / str(config["raw_nuts3_path"])).read_text(encoding="utf-8"))
    regions = [
        {
            "type": "Feature",
            "properties": {
                "id": feature["properties"]["NUTS_ID"],
                "nuts_code": feature["properties"]["NUTS_ID"],
                "district_code": feature["properties"]["NUTS_ID"],
                "district_name": feature["properties"]["NAME_LATN"],
                "name": feature["properties"]["NAME_LATN"],
                "region": config["region_name"],
                "region_abbr": config["region_abbr"],
            },
            "geometry": feature["geometry"],
        }
        for feature in nuts["features"]
        if str(feature["properties"].get("NUTS_ID", "")).startswith(str(config["nuts1"]))
    ]
    return regions


def load_nrw_chargers(config: dict[str, object]) -> tuple[list[dict], str | None]:
    path = ROOT / str(config["raw_bnetza_path"])
    with path.open(encoding=bnetza_csv_encoding(path), newline="") as file:
        preamble = "".join(next(file) for _ in range(CHARGER_PREAMBLE_ROWS))
        rows = list(csv.DictReader(file, delimiter=";"))
    snapshot_date = parse_charger_snapshot_date(preamble)

    features = []
    for row in rows:
        if row.get("Bundesland") != config["region_name"]:
            continue
        lon = parse_decimal(row.get("Längengrad", ""))
        lat = parse_decimal(row.get("Breitengrad", ""))
        power = parse_decimal(row.get("Nennleistung Ladeeinrichtung [kW]", ""))
        maximum_power = max_point_power_kw(row)
        charging_points = int(parse_decimal(row.get("Anzahl Ladepunkte", "")) or 1)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": row.get("Ladeeinrichtungs-ID"),
                    "name": row.get("Anzeigename (Karte)") or row.get("Betreiber") or "Charging station",
                    "operator": row.get("Betreiber"),
                    "status": row.get("Status"),
                    "charger_type": row.get("Art der Ladeeinrichtung"),
                    "charging_points": charging_points,
                    "connectors": charging_points,
                    "power_kw": power,
                    "max_point_power_kw": maximum_power,
                    "district_text": row.get("Kreis/kreisfreie Stadt"),
                    "region": config["region_name"],
                    "state": config["region_name"],
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )
    return features, snapshot_date
