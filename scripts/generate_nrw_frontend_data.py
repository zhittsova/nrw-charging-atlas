from __future__ import annotations

import csv
import io
import json
import math
import tempfile
from pathlib import Path

from config_utils import ROOT, read_simple_region_config
from nrw_charger_quality import EXCEPTION_FIELDS, assert_reconciled, classify_chargers


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


def feature_bbox(feature: dict) -> tuple[float, float, float, float]:
    coords: list[tuple[float, float]] = []

    def collect(values: object) -> None:
        if isinstance(values, list) and values and isinstance(values[0], (int, float)):
            coords.append((float(values[0]), float(values[1])))
        elif isinstance(values, list):
            for value in values:
                collect(value)

    collect(feature["geometry"]["coordinates"])
    xs = [coord[0] for coord in coords]
    ys = [coord[1] for coord in coords]
    return min(xs), min(ys), max(xs), max(ys)


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
                "data_quality_flag": "real_nuts3_geometry_missing_population_roads_grid_renewables",
            },
            "geometry": feature["geometry"],
        }
        for feature in nuts["features"]
        if str(feature["properties"].get("NUTS_ID", "")).startswith(str(config["nuts1"]))
    ]
    for region in regions:
        region["properties"]["box"] = list(feature_bbox(region))
    return regions


def load_nrw_chargers(config: dict[str, object]) -> list[dict]:
    with (ROOT / str(config["raw_bnetza_path"])).open(encoding="cp1252", errors="replace") as file:
        for _ in range(10):
            next(file)
        rows = list(csv.DictReader(file, delimiter=";"))

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
    return features


def enrich_regions_with_counts(regions: list[dict], chargers: list[dict]) -> list[dict]:
    counts = {region["properties"]["nuts_code"]: 0 for region in regions}
    points = {region["properties"]["nuts_code"]: 0 for region in regions}
    fast = {region["properties"]["nuts_code"]: 0 for region in regions}
    normal = {region["properties"]["nuts_code"]: 0 for region in regions}
    unknown = {region["properties"]["nuts_code"]: 0 for region in regions}

    for charger in chargers:
        nuts_code = charger["properties"]["nuts_code"]
        counts[nuts_code] += 1
        points[nuts_code] += int(charger["properties"].get("charging_points") or 1)
        classification = classify_power(charger["properties"].get("max_point_power_kw"))
        if classification == "fast":
            fast[nuts_code] += 1
        elif classification == "normal":
            normal[nuts_code] += 1
        else:
            unknown[nuts_code] += 1

    max_count = max(counts.values()) or 1
    for region in regions:
        nuts_code = region["properties"]["nuts_code"]
        supply_score = round(100 * counts[nuts_code] / max_count, 1)
        priority_score = round(100 - supply_score, 1)
        region["properties"].update(
            stationCount=counts[nuts_code],
            chargers_total=counts[nuts_code],
            charging_points_total=points[nuts_code],
            fast_chargers=fast[nuts_code],
            fast_chargers_total=fast[nuts_code],
            normal_chargers=normal[nuts_code],
            normal_chargers_total=normal[nuts_code],
            unknown_power_chargers=unknown[nuts_code],
            unknown_power_chargers_total=unknown[nuts_code],
            chargingSupplyScore=supply_score,
            charging_supply_score=supply_score,
            demandScore=None,
            demand_score=None,
            accessibilityScore=None,
            accessibility_score=None,
            gridReadinessProxyScore=None,
            grid_readiness_proxy_score=None,
            renewablePotentialScore=None,
            renewable_potential_score=None,
            investmentPriorityScore=priority_score,
            investment_priority_score=priority_score,
            chargerDeficitScore=priority_score,
            charger_deficit_score=priority_score,
            dataQualityScore=35,
            data_quality_score=35,
        )

    ranked = sorted(regions, key=lambda item: item["properties"]["investmentPriorityScore"], reverse=True)
    for rank, region in enumerate(ranked, start=1):
        score = region["properties"]["investmentPriorityScore"]
        tier = "Very High" if score >= 80 else "High" if score >= 65 else "Medium" if score >= 45 else "Low"
        region["properties"]["priorityRank"] = rank
        region["properties"]["priority_rank"] = rank
        region["properties"]["priorityTier"] = tier
        region["properties"]["priority_tier"] = tier
    return regions


def assert_district_totals(regions: list[dict], accepted: list[dict]) -> None:
    district_total = sum(int(region["properties"].get("chargers_total") or 0) for region in regions)
    if district_total != len(accepted):
        raise ValueError(
            f"District station totals ({district_total}) do not equal accepted feature count ({len(accepted)})"
        )


def _write_temporary_text(target: Path, content: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        file.write(content)
        return Path(file.name)


def write_outputs_atomically(
    regions: list[dict],
    accepted: list[dict],
    rejected: list[dict],
    *,
    region_path: Path,
    charger_path: Path,
    exception_path: Path,
) -> None:
    region_content = json.dumps(
        {"type": "FeatureCollection", "name": "nrw_nuts3_regions", "features": regions},
        indent=2,
    )
    charger_content = json.dumps(
        {"type": "FeatureCollection", "name": "nrw_bnetza_chargers", "features": accepted},
        indent=2,
    )
    exception_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(exception_buffer, fieldnames=EXCEPTION_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rejected)

    targets_and_content = [
        (region_path, region_content),
        (charger_path, charger_content),
        (exception_path, exception_buffer.getvalue()),
    ]
    temporary_paths: list[Path] = []
    try:
        for target, content in targets_and_content:
            temporary_paths.append(_write_temporary_text(target, content))
        for temporary, (target, _) in zip(temporary_paths, targets_and_content, strict=True):
            temporary.replace(target)
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)


def generate_validated_outputs(
    regions: list[dict],
    accepted: list[dict],
    rejected: list[dict],
    *,
    source_count: int,
    region_path: Path,
    charger_path: Path,
    exception_path: Path,
) -> None:
    region_codes = {region["properties"]["nuts_code"] for region in regions}
    assert_reconciled(source_count, accepted, rejected, region_codes)
    assert_district_totals(regions, accepted)
    write_outputs_atomically(
        regions,
        accepted,
        rejected,
        region_path=region_path,
        charger_path=charger_path,
        exception_path=exception_path,
    )


def main() -> None:
    config = read_simple_region_config()
    data_dir = ROOT / "frontend" / "data"
    regions = load_nrw_regions(config)
    region_codes = {region["properties"]["nuts_code"] for region in regions}
    if len(regions) != 53 or len(region_codes) != 53:
        raise ValueError(f"Expected 53 unique NRW districts, found {len(regions)} features and {len(region_codes)} codes")

    candidates = load_nrw_chargers(config)
    accepted, rejected = classify_chargers(candidates, regions)
    regions = enrich_regions_with_counts(regions, accepted)
    generate_validated_outputs(
        regions,
        accepted,
        rejected,
        source_count=len(candidates),
        region_path=data_dir / "nrw_regions_sample.geojson",
        charger_path=data_dir / "nrw_charging_stations_sample.geojson",
        exception_path=ROOT / "data" / "quality" / "nrw_charger_exceptions.csv",
    )
    print(f"wrote {len(regions)} NRW NUTS-3 regions")
    print(f"wrote {len(accepted)} valid NRW BNetzA charger records")
    print(f"wrote {len(rejected)} charger exceptions")


if __name__ == "__main__":
    main()
