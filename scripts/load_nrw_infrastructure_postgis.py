from __future__ import annotations

import argparse
import json
import math
import os
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


# Day-type totals for all motor vehicles: all days, working days, holiday
# working days, and Sundays and public holidays.  Straßen.NRW defines these
# fields; the interpretation of a zero below is the project's, not the
# publisher's.
DAY_TYPE_TOTAL_FIELDS = ("DTVKFZA", "DTVKFZW", "DTVKFZU", "DTVKFZS")


def _traffic_value(value: object, *, field: str) -> float | None:
    """Validate one published daily traffic volume.

    A zero is returned as a measured zero here.  Deciding that a reading was
    never published is a property of the whole row, not of one field, and is
    handled by :func:`traffic_observation`.
    """
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite, not {value!r}")
    if number < 0:
        raise ValueError(f"{field} must not be negative")
    return number


def traffic_observation(
    day_type_totals: dict[str, object],
    light: object,
    heavy: object,
) -> tuple[float | None, float | None, float | None]:
    """Return the stored (total, light, heavy) for one counted road section.

    Straßen.NRW defines DTVKFZA as the average daily traffic volume for all
    motor vehicles on all days, DTVLVA as the light-vehicle share and DTVSVA as
    the heavy-vehicle share.  The publisher defines **no** no-data code; the two
    rules below are the project's own inferences and are labelled as such.

    *Row level.* When every day-type total is zero at once, the station
    published nothing for this section and the whole observation is unknown.  In
    the current snapshot all 1,022 such rows sit at manual SVZ counting stations
    and none at an automatic permanent or temporary one, and a classified B, L
    or K road carrying no vehicles on any day type is not a credible
    measurement.  ``counting_station_type`` is stored so the inference stays
    auditable; only the all-days fields are stored, so the day-type
    corroboration is re-checked on every load rather than kept per row.

    *Class level.* A positive total cannot consist of neither light nor heavy
    vehicles, so a zero for both classes under a positive total is an absent
    split.  Any other zero is a real observation and is kept: a road with no
    heavy goods traffic genuinely reports a heavy count of zero.
    """
    totals = {
        field: _traffic_value(value, field=field)
        for field, value in day_type_totals.items()
    }
    all_days = totals.get("DTVKFZA")
    known = [value for value in totals.values() if value is not None]

    if all_days == 0:
        if known and any(value != 0 for value in known):
            raise ValueError(
                "Straßen.NRW section reports a zero all-days total with a positive "
                "day-type total; the unpublished-station rule no longer holds"
            )
        return None, None, None

    light_value = _traffic_value(light, field="DTVLVA")
    heavy_value = _traffic_value(heavy, field="DTVSVA")
    if all_days is not None and all_days > 0 and light_value == 0 and heavy_value == 0:
        return all_days, None, None
    return all_days, light_value, heavy_value


def read_roads(path: Path) -> list[dict]:
    frame = gpd.read_file(f"zip://{path.resolve()}").to_crs(4326)
    rows = []
    for row in frame.itertuples(index=False):
        total, light, heavy = traffic_observation(
            {field: getattr(row, field, None) for field in DAY_TYPE_TOTAL_FIELDS},
            row.DTVLVA,
            row.DTVSVA,
        )
        rows.append(
            {
                "source_id": f"{row.ABS}|{row.ZSTNR}|{row.VSTAT}|{row.BSTAT}",
                "road_class": row.STRKL,
                "name": row.STRBEZ,
                "road_number": row.STRNR,
                "traffic_total": total,
                "traffic_light": light,
                "traffic_heavy": heavy,
                "counting_station_type": row.ZSTART,
                "source": "Straßen.NRW Verkehrswerte",
                "geom_json": geom_json(row.geometry),
            }
        )
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
                        "name": record.get("Name der Einheit"),
                        "operator": record.get("Anlagenbetreiber"),
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
        "traffic_light", "traffic_heavy", "counting_station_type", "source", "geom_json",
    )
    renewable_cols = (
        "source_id", "name", "operator", "asset_type", "technology",
        "capacity_mw", "status", "geom_json",
    )
    return f"""BEGIN;
CREATE TEMP TABLE import_power_plants (
  source_id text, name text, operator text, energy_source text, technology text,
  capacity_mw numeric, status text, voltage text, network_operator text, geom_json text
) ON COMMIT DROP;
CREATE TEMP TABLE import_roads (
  source_id text, road_class text, name text, road_number text, traffic_total numeric,
  traffic_light numeric, traffic_heavy numeric, counting_station_type text,
  source text, geom_json text
) ON COMMIT DROP;
CREATE TEMP TABLE import_renewables (
  source_id text, name text, operator text, asset_type text, technology text,
  capacity_mw numeric, status text, geom_json text
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
INSERT INTO raw.roads (
  osm_id, road_class, name, road_number, traffic_total, traffic_light, traffic_heavy,
  counting_station_type, source, geom
)
SELECT source_id, road_class, name, road_number, traffic_total, traffic_light, traffic_heavy,
       counting_station_type, source,
       ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)::geometry(LineString,4326)
FROM import_roads;
INSERT INTO raw.renewable_assets
  (source_id, name, operator, asset_type, technology, capacity_mw, status, geom)
SELECT source_id, name, operator, asset_type, technology, capacity_mw, status,
       ST_SetSRID(ST_GeomFromGeoJSON(geom_json), 4326)::geometry(Point,4326)
FROM import_renewables;
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required")
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
            (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8"),
        ],
    )
    print(f"loaded OPSD={len(plants)}, roads={len(roads)}, renewables={len(renewables)}")


if __name__ == "__main__":
    main()
