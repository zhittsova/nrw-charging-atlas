from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from datetime import date
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
    "max_point_power_kw",
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
        "max_point_power_kw": properties.get("max_point_power_kw"),
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


def read_raw_seed_snapshot() -> tuple[list[dict], list[dict], dict[str, str] | None]:
    """Read the canonical raw inputs without consulting frontend exports.

    The frontend GeoJSON files are a downstream convenience artifact.  Keeping
    this adapter next to the raw-to-table converter ensures that a seed and a
    frontend export share the same source parsing and charger quality gate,
    without making either stage depend on the other.
    """
    from generate_nrw_frontend_data import load_nrw_chargers, load_nrw_regions
    from config_utils import read_simple_region_config
    from nrw_charger_quality import assert_reconciled, classify_chargers

    config = read_simple_region_config(ROOT / "config" / "regions" / "nrw.yml")
    regions = load_nrw_regions(config)
    candidates, snapshot_date = load_nrw_chargers(config)
    accepted, rejected = classify_chargers(candidates, regions)
    assert_reconciled(
        len(candidates),
        accepted,
        rejected,
        {str(region["properties"]["nuts_code"]) for region in regions},
    )
    snapshot = (
        {
            "source_key": "bnetza_ladesaeulenregister",
            "snapshot_date": snapshot_date,
            "source_name": "Bundesnetzagentur Ladesaeulenregister",
        }
        if snapshot_date is not None
        else None
    )
    return (
        _unique_rows(regions, admin_row, "nuts_code", "admin region"),
        _unique_rows(accepted, charger_row, "source_id", "charger"),
        snapshot,
    )


def read_source_snapshot(path: Path) -> dict[str, str] | None:
    """Return the publication date the generated charger snapshot carries.

    The date is a property of the file the loader consumed, so it is recorded
    once per source rather than copied onto every station.  A snapshot without a
    readable date returns None: the published date then says it was not
    recorded, and no substitute is invented.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    snapshot_date = document.get("snapshot_date")
    if snapshot_date is None:
        return None
    if not isinstance(snapshot_date, str):
        raise ValueError(f"{path} has a non-string snapshot_date")
    try:
        parsed = date.fromisoformat(snapshot_date)
    except ValueError as error:
        raise ValueError(f"{path} has an invalid snapshot_date: {snapshot_date}") from error
    return {
        "source_key": str(document.get("source_key") or "bnetza_ladesaeulenregister"),
        "snapshot_date": parsed.isoformat(),
        "source_name": str(document.get("source_name") or "Bundesnetzagentur Ladesaeulenregister"),
    }


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


def _source_snapshot_block(snapshot: dict[str, str] | None) -> str:
    """Record, or explicitly clear, the ingested source's publication date.

    A load whose snapshot states no date removes any earlier row instead of
    leaving the previous date attached to the new data.
    """
    if snapshot is None:
        return (
            "DELETE FROM raw.source_snapshots\n"
            "WHERE source_key = 'bnetza_ladesaeulenregister';\n"
        )
    values = _copy_block(
        "import_source_snapshots",
        ("source_key", "snapshot_date", "source_name"),
        [snapshot],
    )
    return f"""CREATE TEMP TABLE import_source_snapshots (
    source_key text NOT NULL,
    snapshot_date date NOT NULL,
    source_name text
) ON COMMIT DROP;
{values}
INSERT INTO raw.source_snapshots (source_key, snapshot_date, source_name, source_note)
SELECT
    source_key,
    snapshot_date,
    source_name,
    'Stated in the register preamble as "Letzte Aktualisierung vom"'
FROM import_source_snapshots
ON CONFLICT (source_key) DO UPDATE
SET
    snapshot_date = EXCLUDED.snapshot_date,
    source_name = EXCLUDED.source_name,
    source_note = EXCLUDED.source_note,
    recorded_at = now();
"""


def build_import_script(
    admin_regions: list[dict],
    chargers: list[dict],
    *,
    source_snapshot: dict[str, str] | None = None,
) -> str:
    admin_copy = _copy_block("import_admin_regions", ADMIN_COLUMNS, admin_regions)
    charger_copy = _copy_block("import_chargers", CHARGER_COLUMNS, chargers)
    snapshot_block = _source_snapshot_block(source_snapshot)
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
    max_point_power_kw numeric,
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
    max_point_power_kw,
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
    max_point_power_kw,
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
    max_point_power_kw = EXCLUDED.max_point_power_kw,
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

{snapshot_block}
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


def transaction_body(sql: str, *, label: str) -> str:
    """Remove one document's outer transaction for a coordinated refresh.

    SQL and import builders remain independently executable for their focused
    tests and diagnostics.  The seed coordinator must instead place all of
    them in *one* transaction, so a failure cannot publish a mixture of old
    analytics and new raw rows.
    """
    match = re.search(r"(?m)^BEGIN;\s*$", sql)
    if match is None:
        raise ValueError(f"{label} is missing its outer BEGIN")
    trailing = re.search(r"(?s)\nCOMMIT;\s*\Z", sql)
    if trailing is None or trailing.start() <= match.end():
        raise ValueError(f"{label} is missing its outer COMMIT")
    return sql[: match.start()] + sql[match.end() : trailing.start()] + "\n"


def run_refresh(
    database_url: str,
    *,
    schema_sql: str,
    import_sql: str | Iterable[str],
    analytics_sql: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    psql: str = PSQL,
) -> None:
    """Atomically publish a fully validated candidate refresh in one psql call."""
    import_documents = (import_sql,) if isinstance(import_sql, str) else tuple(import_sql)
    if not import_documents:
        raise ValueError("A refresh requires at least one staged import document")
    documents = (
        transaction_body(schema_sql, label="schema SQL"),
        *(transaction_body(document, label="import SQL") for document in import_documents),
        # The schema creates this materialization before candidate imports so
        # that legacy upgrades have a stable shape.  Refresh it only after
        # every source stage is present; analytics below consumes these exact
        # candidate measurements.
        "REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;\n",
        transaction_body(analytics_sql, label="analytics SQL"),
    )
    coordinated_sql = "\\set ON_ERROR_STOP on\nBEGIN;\n" + "\n".join(documents) + "COMMIT;\n"
    run_psql(
        database_url,
        (coordinated_sql,),
        runner=runner,
        psql=psql,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load validated NRW snapshots into PostGIS")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--schema", type=Path, default=ROOT / "db" / "nrw_schema.sql")
    parser.add_argument("--regions", type=Path, help="Optional legacy GeoJSON regions input")
    parser.add_argument(
        "--chargers",
        type=Path,
        help="Optional legacy GeoJSON chargers input",
    )
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if (args.regions is None) != (args.chargers is None):
        raise ValueError("--regions and --chargers must be supplied together")
    if args.regions is None:
        admin_regions, chargers, source_snapshot = read_raw_seed_snapshot()
    else:
        admin_regions = read_admin_regions(args.regions)
        chargers = read_chargers(args.chargers)
        source_snapshot = read_source_snapshot(args.chargers)
    validate_snapshot(admin_regions, chargers)

    print(f"validated {len(admin_regions)} NRW admin regions")
    print(f"validated {len(chargers)} NRW charging stations")
    if args.check_only:
        return
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required unless --check-only is used")

    raise ValueError(
        "Individual loaders only validate inputs. Use scripts/refresh_nrw_database.py "
        "to publish a complete atomic seed."
    )


if __name__ == "__main__":
    main()
