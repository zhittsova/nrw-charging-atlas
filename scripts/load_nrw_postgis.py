from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

from config_utils import ROOT


PSQL = "psql"

ADMIN_COLUMNS = ("nuts_code", "ags", "district_name", "region_name", "geom_json")
CHARGER_COLUMNS = (
    "source_id",
    "operator",
    "status",
    "charger_type",
    "charging_points",
    "power_kw",
    "street",
    "postcode",
    "city",
    "district_text",
    "bundesland",
    "geom_json",
)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read_feature_collection(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise ValueError(f"{path} is not a GeoJSON FeatureCollection")
    return document["features"]


def admin_row(feature: dict) -> dict[str, object]:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    nuts_code = properties.get("nuts_code") or properties.get("NUTS_ID") or properties.get("id")
    if not nuts_code:
        raise ValueError("Admin region feature is missing nuts_code")
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"Admin region {nuts_code} must have Polygon or MultiPolygon geometry")
    return {
        "nuts_code": str(nuts_code),
        "ags": properties.get("ags"),
        "district_name": properties.get("district_name") or properties.get("NAME_LATN") or properties.get("name"),
        "region_name": properties.get("region") or properties.get("region_name"),
        "geom_json": _compact_json(geometry),
    }


def charger_row(feature: dict) -> dict[str, object]:
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    source_id = properties.get("id") or properties.get("source_id")
    if not source_id:
        raise ValueError("Charging feature is missing id")
    if geometry.get("type") != "Point":
        raise ValueError(f"Charging feature {source_id} must have Point geometry")
    coordinates = geometry.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in coordinates[:2])
        or not all(math.isfinite(float(value)) for value in coordinates[:2])
        or not -180 <= float(coordinates[0]) <= 180
        or not -90 <= float(coordinates[1]) <= 90
    ):
        raise ValueError(f"Charging feature {source_id} has invalid Point coordinates")
    return {
        "source_id": str(source_id),
        "operator": properties.get("operator"),
        "status": properties.get("status"),
        "charger_type": properties.get("charger_type"),
        "charging_points": properties.get("charging_points"),
        "power_kw": properties.get("power_kw"),
        "street": properties.get("street"),
        "postcode": properties.get("postcode"),
        "city": properties.get("city"),
        "district_text": properties.get("district_text"),
        "bundesland": properties.get("state") or properties.get("bundesland") or properties.get("region"),
        "geom_json": _compact_json(geometry),
    }


def _unique_rows(
    features: Iterable[dict],
    converter: Callable[[dict], dict[str, object]],
    key: str,
    label: str,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for feature in features:
        row = converter(feature)
        value = str(row[key])
        if value in seen:
            raise ValueError(f"Duplicate {label} key: {value}")
        seen.add(value)
        rows.append(row)
    return rows


def read_admin_regions(path: Path) -> list[dict]:
    return _unique_rows(_read_feature_collection(path), admin_row, "nuts_code", "admin region")


def read_chargers(path: Path) -> list[dict]:
    return _unique_rows(_read_feature_collection(path), charger_row, "source_id", "charger")


def validate_snapshot(admin_regions: list[dict], chargers: list[dict], expected_admin_count: int = 53) -> None:
    if not chargers:
        raise ValueError("Charging snapshot is empty; refusing destructive synchronization")
    if len(admin_regions) != expected_admin_count:
        raise ValueError(f"Expected {expected_admin_count} NRW admin regions, found {len(admin_regions)}")
    invalid_codes = sorted(
        str(row["nuts_code"]) for row in admin_regions if not str(row["nuts_code"]).startswith("DEA")
    )
    if invalid_codes:
        raise ValueError(f"Admin snapshot contains non-NRW NUTS codes: {', '.join(invalid_codes)}")


def _copy_block(table: str, columns: tuple[str, ...], rows: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return (
        f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true);\n"
        f"{output.getvalue()}\\.\n"
    )


def build_import_script(admin_regions: list[dict], chargers: list[dict]) -> str:
    admin_copy = _copy_block("import_admin_regions", ADMIN_COLUMNS, admin_regions)
    charger_copy = _copy_block("import_chargers", CHARGER_COLUMNS, chargers)
    return f"""BEGIN;
CREATE TEMP TABLE import_admin_regions (
    nuts_code text NOT NULL,
    ags text,
    district_name text,
    region_name text,
    geom_json text NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE import_chargers (
    source_id text NOT NULL,
    operator text,
    status text,
    charger_type text,
    charging_points integer,
    power_kw numeric,
    street text,
    postcode text,
    city text,
    district_text text,
    bundesland text,
    geom_json text NOT NULL
) ON COMMIT DROP;
{admin_copy}{charger_copy}
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom)
SELECT
    nuts_code,
    NULLIF(ags, ''),
    NULLIF(district_name, ''),
    NULLIF(region_name, ''),
    ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)))::geometry(MultiPolygon, 4326)
FROM import_admin_regions
ON CONFLICT (nuts_code) DO UPDATE
SET
    ags = EXCLUDED.ags,
    district_name = EXCLUDED.district_name,
    region_name = EXCLUDED.region_name,
    geom = EXCLUDED.geom;

DELETE FROM raw.admin_regions AS existing
WHERE NOT EXISTS (
    SELECT 1
    FROM import_admin_regions AS incoming
    WHERE incoming.nuts_code = existing.nuts_code
);

INSERT INTO raw.chargers (
    source_id,
    operator,
    status,
    charger_type,
    charging_points,
    power_kw,
    street,
    postcode,
    city,
    district_text,
    bundesland,
    geom
)
SELECT
    source_id,
    NULLIF(operator, ''),
    NULLIF(status, ''),
    NULLIF(charger_type, ''),
    charging_points,
    power_kw,
    NULLIF(street, ''),
    NULLIF(postcode, ''),
    NULLIF(city, ''),
    NULLIF(district_text, ''),
    NULLIF(bundesland, ''),
    ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326))::geometry(Point, 4326)
FROM import_chargers
ON CONFLICT (source_id) DO UPDATE
SET
    operator = EXCLUDED.operator,
    status = EXCLUDED.status,
    charger_type = EXCLUDED.charger_type,
    charging_points = EXCLUDED.charging_points,
    power_kw = EXCLUDED.power_kw,
    street = EXCLUDED.street,
    postcode = EXCLUDED.postcode,
    city = EXCLUDED.city,
    district_text = EXCLUDED.district_text,
    bundesland = EXCLUDED.bundesland,
    geom = EXCLUDED.geom;

DELETE FROM raw.chargers AS existing
WHERE NOT EXISTS (
    SELECT 1
    FROM import_chargers AS incoming
    WHERE incoming.source_id = existing.source_id
);

REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;
COMMIT;
"""


def run_psql(
    database_url: str,
    sql_documents: Iterable[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    psql: str = PSQL,
) -> None:
    for sql in sql_documents:
        result = runner(
            [psql, "--dbname", database_url, "--no-psqlrc", "--set", "ON_ERROR_STOP=1"],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            diagnostics = (result.stderr or result.stdout or "psql failed without diagnostics").strip()
            raise RuntimeError(diagnostics)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load validated NRW snapshots into PostGIS")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--schema", type=Path, default=ROOT / "db" / "nrw_schema.sql")
    parser.add_argument(
        "--regions",
        type=Path,
        default=ROOT / "frontend" / "data" / "nrw_regions_sample.geojson",
    )
    parser.add_argument(
        "--chargers",
        type=Path,
        default=ROOT / "frontend" / "data" / "nrw_charging_stations_sample.geojson",
    )
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    admin_regions = read_admin_regions(args.regions)
    chargers = read_chargers(args.chargers)
    validate_snapshot(admin_regions, chargers)

    print(f"validated {len(admin_regions)} NRW admin regions")
    print(f"validated {len(chargers)} NRW charging stations")
    if args.check_only:
        return
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required unless --check-only is used")

    schema_sql = args.schema.read_text(encoding="utf-8")
    run_psql(
        args.database_url,
        [f"BEGIN;\n{schema_sql}\nCOMMIT;\n", build_import_script(admin_regions, chargers)],
    )
    print("loaded raw.admin_regions and raw.chargers")


if __name__ == "__main__":
    main()
