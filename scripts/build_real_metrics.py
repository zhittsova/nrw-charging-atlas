from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


def read_nuts3() -> gpd.GeoDataFrame:
    nuts = gpd.read_file(RAW / "nuts3_regions_gisco_2024.geojson")
    return (
        nuts.loc[lambda df: df["CNTR_CODE"].eq("DE")]
        .rename(columns={"NUTS_ID": "nuts3_id", "NAME_LATN": "region_name"})
        .loc[:, ["nuts3_id", "region_name", "geometry"]]
        .to_crs(3035)
        .assign(area_km2=lambda df: df.geometry.area / 1_000_000)
    )


def read_chargers() -> gpd.GeoDataFrame:
    chargers = pd.read_csv(
        RAW / "bnetza_ladesaeulenregister.csv",
        sep=";",
        encoding="cp1252",
        skiprows=10,
        low_memory=False,
    )
    lon_col = next(column for column in chargers.columns if "Längengrad" in column or "Longitude" in column)
    lat_col = next(column for column in chargers.columns if "Breitengrad" in column or "Latitude" in column)
    return (
        chargers.assign(
            longitude=lambda df: pd.to_numeric(df[lon_col].astype(str).str.replace(",", "."), errors="coerce"),
            latitude=lambda df: pd.to_numeric(df[lat_col].astype(str).str.replace(",", "."), errors="coerce"),
        )
        .dropna(subset=["longitude", "latitude"])
        .pipe(
            lambda df: gpd.GeoDataFrame(
                df,
                geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
                crs="EPSG:4326",
            )
        )
        .to_crs(3035)
    )


def build_region_charger_metrics() -> gpd.GeoDataFrame:
    regions = read_nuts3()
    chargers = read_chargers()
    joined = gpd.sjoin(chargers.loc[:, ["geometry"]], regions.loc[:, ["nuts3_id", "geometry"]], predicate="within")

    charger_counts = (
        joined.groupby("nuts3_id")
        .size()
        .rename("charging_stations")
        .reset_index()
    )

    return (
        regions.merge(charger_counts, on="nuts3_id", how="left")
        .assign(
            charging_stations=lambda df: df["charging_stations"].fillna(0).astype(int),
            chargers_per_km2=lambda df: df["charging_stations"] / df["area_km2"],
        )
        .sort_values("charging_stations", ascending=False)
    )


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    metrics = build_region_charger_metrics()
    metrics.to_file(PROCESSED / "nuts3_charger_metrics.geojson", driver="GeoJSON")
    metrics.drop(columns="geometry").to_csv(PROCESSED / "nuts3_charger_metrics.csv", index=False)
    print(f"wrote {len(metrics)} NUTS-3 rows")


if __name__ == "__main__":
    main()
