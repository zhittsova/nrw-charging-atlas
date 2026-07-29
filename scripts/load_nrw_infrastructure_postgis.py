from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from config_utils import ROOT
from load_nrw_postgis import _copy_block, run_psql


def geom_json(geometry: object) -> str:
    return json.dumps(mapping(geometry), ensure_ascii=False, separators=(",", ":"))


def read_opsd(path: Path) -> list[dict]:
    frame = pd.read_csv(path)
    frame = frame.loc[
        frame["state"].eq("Nordrhein-Westfalen")
        & frame["lat"].notna()
        & frame["lon"].notna()
        & frame["id"].notna()
    ]
    return [
        {
            "source_id": str(row.id),
            "name": row.name_bnetza,
            "operator": row.company,
            "energy_source": row.energy_source,
            "technology": row.technology,
            "capacity_mw": row.capacity_net_bnetza,
            "status": row.status,
            "voltage": row.voltage,
            "network_operator": row.network_operator,
            "geom_json": json.dumps(
                {"type": "Point", "coordinates": [float(row.lon), float(row.lat)]},
                separators=(",", ":"),
            ),
        }
        for row in frame.itertuples(index=False)
    ]


def read_roads(path: Path) -> list[dict]:
    frame = gpd.read_file(f"zip://{path.resolve()}").to_crs(4326)
    rows = [
        {
            "source_id": f"{row.ABS}|{row.ZSTNR}|{row.VSTAT}|{row.BSTAT}",
            "road_class": row.STRKL,
            "name": row.STRBEZ,
            "road_number": row.STRNR,
            "traffic_total": row.DTVKFZA,
            "traffic_light": row.DTVLVA,
            "traffic_heavy": row.DTVSVA,
            "source": "Straßen.NRW Verkehrswerte",
            "geom_json": geom_json(row.geometry),
        }
        for row in frame.itertuples(index=False)
    ]
    if len({row["source_id"] for row in rows}) != len(rows):
        raise ValueError("Straßen.NRW traffic snapshot contains duplicate composite keys")
    return rows


def read_renewables(path: Path) -> list[dict]:
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as directory:
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1 or not members[0].lower().endswith(".gpkg"):
                raise ValueError("Expected one GeoPackage in renewable-site archive")
            archive.extractall(directory)
        gpkg = Path(directory) / members[0]
        for layer in gpd.list_layers(gpkg)["name"]:
            frame = gpd.read_file(gpkg, layer=layer).to_crs(4326)
            for _, record in frame.iterrows():
                capacity_kw = record.get("Bruttoleistung el. [kW]")
                rows.append(
                    {
                        "source_id": f"{layer}|{record['Einheitennummer']}",
                        "asset_type": record.get("Energieträger"),
                        "technology": layer,
                        "capacity_mw": float(capacity_kw) / 1000 if pd.notna(capacity_kw) else None,
                        "status": record.get("Status"),
                        "geom_json": geom_json(record["geometry"]),
                    }
                )
    ids = [row["source_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Energieatlas snapshot contains duplicate Einheitennummer keys")
    return rows


def build_import_script(plants: list[dict], roads: list[dict], renewables: list[dict]) -> str:
    plant_cols = (
        "source_id", "name", "operator", "energy_source", "technology",
        "capacity_mw", "status", "voltage", "network_operator", "geom_json",
    )
    road_cols = (
        "source_id", "road_class", "name", "road_number", "traffic_total",
        "traffic_light", "traffic_heavy", "source", "geom_json",
    )
    renewable_cols = ("source_id", "asset_type", "technology", "capacity_mw", "status", "geom_json")
    return f"""BEGIN;
CREATE TEMP TABLE import_power_plants (
  source_id text, name text, operator text, energy_source text, technology text,
  capacity_mw numeric, status text, voltage text, network_operator text, geom_json text
) ON COMMIT DROP;
CREATE TEMP TABLE import_roads (
  source_id text, road_class text, name text, road_number text, traffic_total numeric,
  traffic_light numeric, traffic_heavy numeric, source text, geom_json text
) ON COMMIT DROP;
CREATE TEMP TABLE import_renewables (
  source_id text, asset_type text, technology text, capacity_mw numeric, status text, geom_json text
) ON COMMIT DROP;
{_copy_block("import_power_plants", plant_cols, plants)}
{_copy_block("import_roads", road_cols, roads)}
{_copy_block("import_renewables", renewable_cols, renewables)}
TRUNCATE raw.power_plants, raw.roads, raw.renewable_assets;
INSERT INTO raw.power_plants
SELECT source_id, name, operator, energy_source, technology, capacity_mw, status,
       voltage, network_operator,
       ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)::geometry(Point,4326)
FROM import_power_plants;
INSERT INTO raw.roads (osm_id, road_class, name, road_number, traffic_total, traffic_light, traffic_heavy, source, geom)
SELECT source_id, road_class, name, road_number, traffic_total, traffic_light, traffic_heavy, source,
       ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)::geometry(LineString,4326)
FROM import_roads;
INSERT INTO raw.renewable_assets (source_id, asset_type, technology, capacity_mw, status, geom)
SELECT source_id, asset_type, technology, capacity_mw, status,
       ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)::geometry(Point,4326)
FROM import_renewables;
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    plants = read_opsd(ROOT / "data/raw/opsd_conventional_power_plants_de.csv")
    roads = read_roads(ROOT / "data/raw/nrw_infrastructure/transport/Verkehrswerte_EPSG25832_Shape.zip")
    renewables = read_renewables(
        ROOT / "data/raw/nrw_infrastructure/renewables/Standorte-Strom-EE-NRW_EPSG25832_GeoPackage.zip"
    )
    run_psql(
        args.database_url,
        [
            (ROOT / "db/nrw_schema.sql").read_text(encoding="utf-8"),
            build_import_script(plants, roads, renewables),
        ],
    )
    print(f"loaded OPSD={len(plants)}, roads={len(roads)}, renewables={len(renewables)}")


if __name__ == "__main__":
    main()
