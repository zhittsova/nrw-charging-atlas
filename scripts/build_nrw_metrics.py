from __future__ import annotations

from pathlib import Path
import math

import geopandas as gpd
import pandas as pd

from config_utils import ROOT, read_simple_region_config


RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


def normalize_0_100(series: pd.Series, inverse: bool = False) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce")
    if clean.notna().sum() == 0 or clean.max() == clean.min():
        score = pd.Series(50, index=series.index, dtype="float")
    else:
        score = 100 * (clean - clean.min()) / (clean.max() - clean.min())
    return 100 - score if inverse else score


def weighted_score(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    available = frame[list(weights)].notna()
    weighted_values = sum(frame[column].fillna(0) * weight for column, weight in weights.items())
    available_weights = sum(available[column] * weight for column, weight in weights.items())
    return (weighted_values / available_weights.replace(0, pd.NA)).round(1)


def priority_tier(score: float) -> str:
    if pd.isna(score):
        return "Unknown"
    if score >= 80:
        return "Very High"
    if score >= 65:
        return "High"
    if score >= 45:
        return "Medium"
    return "Low"


def read_nrw_districts(config: dict[str, object]) -> gpd.GeoDataFrame:
    nuts = gpd.read_file(ROOT / str(config["raw_nuts3_path"]))
    projected_crs = str(config["projected_crs"])
    nuts1 = str(config["nuts1"])
    return (
        nuts.loc[lambda df: df["NUTS_ID"].astype(str).str.startswith(nuts1)]
        .rename(columns={"NUTS_ID": "nuts_code", "NAME_LATN": "district_name"})
        .loc[:, ["nuts_code", "district_name", "geometry"]]
        .to_crs(projected_crs)
        .assign(
            district_code=lambda df: df["nuts_code"],
            area_km2=lambda df: df.geometry.area / 1_000_000,
            centroid_geom=lambda df: df.geometry.centroid,
        )
    )


def read_nrw_chargers(config: dict[str, object]) -> gpd.GeoDataFrame:
    chargers = pd.read_csv(
        ROOT / str(config["raw_bnetza_path"]),
        sep=";",
        encoding="cp1252",
        skiprows=10,
        low_memory=False,
    )
    lon_col = next(column for column in chargers.columns if "Längengrad" in column or "Longitude" in column)
    lat_col = next(column for column in chargers.columns if "Breitengrad" in column or "Latitude" in column)
    power_col = next(column for column in chargers.columns if "Nennleistung Ladeeinrichtung" in column)
    connector_columns = [
        column for column in chargers.columns if column.startswith("Nennleistung Stecker")
    ]
    if connector_columns:
        connector_power = chargers.loc[:, connector_columns].apply(
            lambda column: pd.to_numeric(column.astype(str).str.replace(",", "."), errors="coerce")
        )
        connector_power = connector_power.where(connector_power.apply(lambda column: column.map(math.isfinite)))
        connector_power = connector_power.where(connector_power > 0)
        maximum_point_power = connector_power.max(axis=1)
    else:
        maximum_point_power = pd.Series(pd.NA, index=chargers.index, dtype="Float64")

    return (
        chargers.loc[lambda df: df["Bundesland"].eq(config["region_name"])]
        .assign(
            longitude=lambda df: pd.to_numeric(df[lon_col].astype(str).str.replace(",", "."), errors="coerce"),
            latitude=lambda df: pd.to_numeric(df[lat_col].astype(str).str.replace(",", "."), errors="coerce"),
            charging_points=lambda df: pd.to_numeric(df["Anzahl Ladepunkte"], errors="coerce").fillna(1).astype(int),
            power_kw=lambda df: pd.to_numeric(df[power_col].astype(str).str.replace(",", "."), errors="coerce"),
            max_point_power_kw=maximum_point_power,
            source_id=lambda df: df["Ladeeinrichtungs-ID"].astype(str),
            operator=lambda df: df["Betreiber"],
            district_text=lambda df: df["Kreis/kreisfreie Stadt"],
        )
        .dropna(subset=["longitude", "latitude"])
        .pipe(
            lambda df: gpd.GeoDataFrame(
                df,
                geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
                crs=str(config["web_crs"]),
            )
        )
        .to_crs(str(config["projected_crs"]))
    )


def build_charging_metrics(districts: gpd.GeoDataFrame, chargers: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    joined = gpd.sjoin(
        chargers.loc[:, ["source_id", "charging_points", "power_kw", "max_point_power_kw", "geometry"]],
        districts.loc[:, ["nuts_code", "geometry"]],
        predicate="within",
        how="inner",
    )
    grouped = (
        joined.assign(
            fast_charger=lambda df: df["max_point_power_kw"].ge(50),
            normal_charger=lambda df: df["max_point_power_kw"].lt(50),
            unknown_power_charger=lambda df: df["max_point_power_kw"].isna(),
        )
        .groupby("nuts_code")
        .agg(
            chargers_total=("source_id", "nunique"),
            charging_points_total=("charging_points", "sum"),
            fast_chargers_total=("fast_charger", "sum"),
            normal_chargers_total=("normal_charger", "sum"),
            unknown_power_chargers_total=("unknown_power_charger", "sum"),
        )
        .reset_index()
    )
    return (
        districts.drop(columns="centroid_geom")
        .merge(grouped, on="nuts_code", how="left")
        .assign(
            chargers_total=lambda df: df["chargers_total"].fillna(0).astype(int),
            charging_points_total=lambda df: df["charging_points_total"].fillna(0).astype(int),
            fast_chargers_total=lambda df: df["fast_chargers_total"].fillna(0).astype(int),
            normal_chargers_total=lambda df: df["normal_chargers_total"].fillna(0).astype(int),
            unknown_power_chargers_total=lambda df: df["unknown_power_chargers_total"].fillna(0).astype(int),
            chargers_per_km2=lambda df: df["chargers_total"] / df["area_km2"],
            charging_points_per_km2=lambda df: df["charging_points_total"] / df["area_km2"],
            share_fast_chargers=lambda df: df["fast_chargers_total"] / df["chargers_total"].replace(0, pd.NA),
        )
    )


def add_proxy_scores(metrics: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    scored = metrics.assign(
        charging_supply_score=lambda df: (
            0.55 * normalize_0_100(df["charging_points_per_km2"])
            + 0.30 * normalize_0_100(df["chargers_per_km2"])
            + 0.15 * normalize_0_100(df["share_fast_chargers"].fillna(0))
        ).round(1),
        population=pd.NA,
        population_density=pd.NA,
        demand_proxy_score=pd.NA,
        demand_score=pd.NA,
        road_access_score=pd.NA,
        accessibility_score=pd.NA,
        grid_readiness_proxy_score=pd.NA,
        renewable_potential_score=pd.NA,
        data_quality_score=35,
        data_quality_flag="missing_population_roads_grid_renewables",
    )

    scored["investment_priority_score"] = (100 - scored["charging_supply_score"]).round(1)
    scored["priority_rank"] = scored["investment_priority_score"].rank(method="dense", ascending=False).astype(int)
    scored["priority_tier"] = scored["investment_priority_score"].map(priority_tier)
    return scored.sort_values("priority_rank")


def main() -> None:
    config = read_simple_region_config()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    districts = read_nrw_districts(config)
    chargers = read_nrw_chargers(config)
    metrics = build_charging_metrics(districts, chargers)
    scores = add_proxy_scores(metrics).to_crs(str(config["web_crs"]))

    scores.to_file(ROOT / str(config["processed_metrics_path"]), driver="GeoJSON")
    scores.drop(columns="geometry").to_csv(ROOT / str(config["processed_scores_path"]), index=False)
    print(f"wrote {len(scores)} NRW districts")
    print(f"used {len(chargers)} NRW charger records")


if __name__ == "__main__":
    main()
