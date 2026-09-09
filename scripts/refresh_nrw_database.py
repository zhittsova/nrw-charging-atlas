"""Validate every cached source, then publish it as one atomic NRW refresh."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from config_utils import ROOT
from load_nrw_energy_balance_postgis import (
    ENERGY_WORKBOOK,
    build_district_lookup,
    build_import_script as energy_import_script,
    read_energy_workbook,
)
from load_nrw_grid_postgis import (
    GRID_PBF,
    build_import_script as grid_import_script,
    extract_grid_geojson,
    read_grid_geojson,
)
from load_nrw_infrastructure_postgis import (
    build_import_script as infrastructure_import_script,
    read_opsd,
    read_renewables,
    read_roads,
)
from load_nrw_population_postgis import read_population_snapshot
from load_nrw_postgis import (
    build_import_script as charger_import_script,
    read_raw_seed_snapshot,
    run_refresh,
    validate_snapshot,
)
from load_nrw_road_network_postgis import (
    ROAD_PBF,
    build_import_script as road_import_script,
    extract_road_geojson,
    read_road_geojson,
)


POPULATION_SNAPSHOT = ROOT / "data" / "raw" / "eurostat_population_nrw.json"
OPSD_SNAPSHOT = ROOT / "data" / "raw" / "opsd_conventional_power_plants_de.csv"
TRAFFIC_SNAPSHOT = (
    ROOT / "data" / "raw" / "nrw_infrastructure" / "transport" / "Verkehrswerte_EPSG25832_Shape.zip"
)
RENEWABLE_SNAPSHOT = (
    ROOT
    / "data"
    / "raw"
    / "nrw_infrastructure"
    / "renewables"
    / "Standorte-Strom-EE-NRW_EPSG25832_GeoPackage.zip"
)


def read_grid_rows(*, pbf: Path, geojson: Path | None) -> list[dict[str, object]]:
    if geojson is not None:
        return read_grid_geojson(geojson)
    with tempfile.TemporaryDirectory(prefix="nrw-refresh-grid-") as directory:
        export = Path(directory) / "grid.geojson"
        extract_grid_geojson(pbf, export)
        return read_grid_geojson(export)


def read_road_rows(*, pbf: Path, geojson: Path | None) -> list[dict[str, object]]:
    if geojson is not None:
        return read_road_geojson(geojson)
    with tempfile.TemporaryDirectory(prefix="nrw-refresh-roads-") as directory:
        export = Path(directory) / "roads.geojson"
        extract_road_geojson(pbf, export)
        return read_road_geojson(export)


def build_seed_imports(
    *,
    population_snapshot: Path = POPULATION_SNAPSHOT,
    opsd_snapshot: Path = OPSD_SNAPSHOT,
    traffic_snapshot: Path = TRAFFIC_SNAPSHOT,
    renewable_snapshot: Path = RENEWABLE_SNAPSHOT,
    energy_workbook: Path = ENERGY_WORKBOOK,
    grid_pbf: Path = GRID_PBF,
    road_pbf: Path = ROAD_PBF,
    grid_geojson: Path | None = None,
    road_geojson: Path | None = None,
) -> tuple[str, ...]:
    """Validate all raw sources before any database transaction begins."""
    admin_regions, chargers, source_snapshot = read_raw_seed_snapshot()
    validate_snapshot(admin_regions, chargers)
    expected_codes = {str(region["nuts_code"]) for region in admin_regions}
    population = read_population_snapshot(population_snapshot, expected_codes=expected_codes)
    plants = read_opsd(opsd_snapshot)
    traffic = read_roads(traffic_snapshot)
    renewables = read_renewables(renewable_snapshot)
    grid = read_grid_rows(pbf=grid_pbf, geojson=grid_geojson)
    roads = read_road_rows(pbf=road_pbf, geojson=road_geojson)
    consumption, energy_renewables, reporting_year = read_energy_workbook(
        energy_workbook, build_district_lookup(admin_regions)
    )

    population_columns = (
        "district_code", "nuts_code", "ags", "population", "reference_year", "source"
    )
    from load_nrw_postgis import _copy_block

    population_import = f"""BEGIN;
CREATE TEMP TABLE import_population (
  district_code text, nuts_code text, ags text, population integer,
  reference_year integer, source text
) ON COMMIT DROP;
{_copy_block("import_population", population_columns, population)}
TRUNCATE raw.population;
INSERT INTO raw.population
SELECT district_code, nuts_code, NULLIF(ags, ''), population, reference_year, source
FROM import_population;
COMMIT;
"""
    return (
        charger_import_script(admin_regions, chargers, source_snapshot=source_snapshot),
        population_import,
        infrastructure_import_script(plants, traffic, renewables),
        grid_import_script(grid),
        road_import_script(roads),
        energy_import_script(consumption, energy_renewables, reporting_year),
    )


def refresh_database(database_url: str, **paths: Path | None) -> None:
    imports = build_seed_imports(**paths)
    run_refresh(
        database_url,
        schema_sql=(ROOT / "db" / "nrw_schema.sql").read_text(encoding="utf-8"),
        import_sql=imports,
        analytics_sql=(ROOT / "db" / "nrw_analytics.sql").read_text(encoding="utf-8"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atomically refresh the NRW database from cached raw sources")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--population-snapshot", type=Path, default=POPULATION_SNAPSHOT)
    parser.add_argument("--opsd-snapshot", type=Path, default=OPSD_SNAPSHOT)
    parser.add_argument("--traffic-snapshot", type=Path, default=TRAFFIC_SNAPSHOT)
    parser.add_argument("--renewable-snapshot", type=Path, default=RENEWABLE_SNAPSHOT)
    parser.add_argument("--energy-workbook", type=Path, default=ENERGY_WORKBOOK)
    parser.add_argument("--grid-pbf", type=Path, default=GRID_PBF)
    parser.add_argument("--road-pbf", type=Path, default=ROAD_PBF)
    parser.add_argument("--grid-geojson", type=Path)
    parser.add_argument("--road-geojson", type=Path)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = {
        "population_snapshot": args.population_snapshot,
        "opsd_snapshot": args.opsd_snapshot,
        "traffic_snapshot": args.traffic_snapshot,
        "renewable_snapshot": args.renewable_snapshot,
        "energy_workbook": args.energy_workbook,
        "grid_pbf": args.grid_pbf,
        "road_pbf": args.road_pbf,
        "grid_geojson": args.grid_geojson,
        "road_geojson": args.road_geojson,
    }
    imports = build_seed_imports(**paths)
    print(f"validated {len(imports)} raw source stages")
    if args.check_only:
        return
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required unless --check-only is used")
    run_refresh(
        args.database_url,
        schema_sql=(ROOT / "db" / "nrw_schema.sql").read_text(encoding="utf-8"),
        import_sql=imports,
        analytics_sql=(ROOT / "db" / "nrw_analytics.sql").read_text(encoding="utf-8"),
    )
    print("published one validated NRW refresh")


if __name__ == "__main__":
    main()
