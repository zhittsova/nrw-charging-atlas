from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from config_utils import ROOT
from load_nrw_postgis import _copy_block


GRID_PBF = ROOT / "data/raw/nrw_infrastructure/grid/nordrhein-westfalen-latest.osm.pbf"
GRID_ASSET_TYPES = {"line", "cable", "minor_line", "substation", "transformer"}


def osmium_filter_command(source: Path, target: Path) -> list[str]:
    return [
        "osmium",
        "tags-filter",
        str(source),
        "w/power=line,cable,minor_line",
        "nwr/power=substation,transformer",
        "--overwrite",
        "-o",
        str(target),
    ]


def extract_grid_geojson(source: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="nrw-grid-") as directory:
        filtered = Path(directory) / "power-grid.osm.pbf"
        subprocess.run(osmium_filter_command(source, filtered), check=True)
        subprocess.run(
            ["osmium", "export", str(filtered), "--overwrite", "-o", str(target)],
            check=True,
        )


def read_grid_geojson(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("type") != "FeatureCollection":
        raise ValueError("OSM grid export must be a GeoJSON FeatureCollection")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        asset_type = str(properties.get("power") or "")
        if asset_type not in GRID_ASSET_TYPES:
            continue
        source_id = str(feature.get("id") or properties.get("@id") or "")
        geometry = feature.get("geometry")
        if not source_id or not isinstance(geometry, dict) or not geometry.get("type"):
            raise ValueError("OSM grid feature is missing id or geometry")
        if source_id in seen:
            raise ValueError(f"OSM grid export contains duplicate id: {source_id}")
        seen.add(source_id)
        rows.append(
            {
                "source_id": source_id,
                "asset_type": asset_type,
                "voltage": properties.get("voltage"),
                "name": properties.get("name"),
                "geom_json": json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
            }
        )
    if not rows:
        raise ValueError("OSM grid export contains no supported power assets")
    return rows


def build_import_script(rows: list[dict[str, object]]) -> str:
    columns = ("source_id", "asset_type", "voltage", "name", "geom_json")
    return f"""BEGIN;
CREATE TEMP TABLE import_grid (
    source_id text NOT NULL,
    asset_type text NOT NULL,
    voltage text,
    name text,
    geom_json text NOT NULL
) ON COMMIT DROP;
{_copy_block("import_grid", columns, rows)}
TRUNCATE raw.grid_infrastructure;
INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom)
SELECT
    source_id,
    asset_type,
    NULLIF(voltage, ''),
    NULLIF(name, ''),
    ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326))::geometry(Geometry, 4326)
FROM import_grid;
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and load the NRW OSM power grid into PostGIS")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--pbf", type=Path, default=GRID_PBF)
    parser.add_argument("--geojson", type=Path, help="Use an existing osmium GeoJSON export")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    if args.geojson:
        rows = read_grid_geojson(args.geojson)
    else:
        with tempfile.TemporaryDirectory(prefix="nrw-grid-") as directory:
            export = Path(directory) / "power-grid.geojson"
            extract_grid_geojson(args.pbf, export)
            rows = read_grid_geojson(export)
    print(f"validated {len(rows)} NRW OSM power-grid assets")
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
