from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from config_utils import ROOT
from load_nrw_postgis import _copy_block, run_psql


ROAD_PBF = ROOT / "data/raw/nrw_infrastructure/grid/nordrhein-westfalen-latest.osm.pbf"


def osmium_filter_command(source: Path, target: Path) -> list[str]:
    return [
        "osmium",
        "tags-filter",
        str(source),
        "w/highway=motorway",
        "--overwrite",
        "-o",
        str(target),
    ]


def extract_road_geojson(source: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="nrw-roads-") as directory:
        filtered = Path(directory) / "autobahns.osm.pbf"
        subprocess.run(osmium_filter_command(source, filtered), check=True)
        subprocess.run(
            ["osmium", "export", str(filtered), "--overwrite", "-o", str(target)],
            check=True,
        )


def read_road_geojson(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("type") != "FeatureCollection":
        raise ValueError("OSM road export must be a GeoJSON FeatureCollection")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        if properties.get("highway") != "motorway":
            continue
        source_id = str(feature.get("id") or properties.get("@id") or "")
        geometry = feature.get("geometry")
        if not source_id:
            raise ValueError("OSM motorway feature is missing an id")
        if source_id in seen:
            raise ValueError(f"OSM road export contains duplicate id: {source_id}")
        if not isinstance(geometry, dict) or geometry.get("type") not in {"LineString", "MultiLineString"}:
            raise ValueError(f"OSM motorway {source_id} must have line geometry")
        seen.add(source_id)
        rows.append(
            {
                "source_id": source_id,
                "highway": "motorway",
                "ref": properties.get("ref"),
                "name": properties.get("name"),
                "geom_json": json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
            }
        )
    if not rows:
        raise ValueError("OSM road export contains no motorway lines")
    return rows


def build_import_script(rows: list[dict[str, object]]) -> str:
    columns = ("source_id", "highway", "ref", "name", "geom_json")
    return f"""BEGIN;
CREATE TEMP TABLE import_osm_roads (
    source_id text NOT NULL,
    highway text NOT NULL,
    ref text,
    name text,
    geom_json text NOT NULL
) ON COMMIT DROP;
{_copy_block("import_osm_roads", columns, rows)}
TRUNCATE raw.osm_roads;
INSERT INTO raw.osm_roads (source_id, highway, ref, name, geom)
SELECT
    source_id,
    highway,
    NULLIF(ref, ''),
    NULLIF(name, ''),
    ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326))::geometry(Geometry, 4326)
FROM import_osm_roads;
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and load NRW Autobahns from OpenStreetMap")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--pbf", type=Path, default=ROAD_PBF)
    parser.add_argument("--geojson", type=Path, help="Use an existing osmium GeoJSON export")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    if args.geojson:
        rows = read_road_geojson(args.geojson)
    else:
        with tempfile.TemporaryDirectory(prefix="nrw-roads-") as directory:
            export = Path(directory) / "autobahns.geojson"
            extract_road_geojson(args.pbf, export)
            rows = read_road_geojson(export)
    print(f"validated {len(rows)} NRW OSM Autobahn features")
    if args.check_only:
        return
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required unless --check-only is used")
    run_psql(
        args.database_url,
        [
            (ROOT / "db/nrw_schema.sql").read_text(encoding="utf-8"),
            build_import_script(rows),
            (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8"),
        ],
    )
    print("loaded raw.osm_roads")


if __name__ == "__main__":
    main()
