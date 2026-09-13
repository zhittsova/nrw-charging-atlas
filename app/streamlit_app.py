"""Historical parallel dashboard. Superseded by the Leaflet/TypeScript frontend.

It reads whichever screening scores `scripts/build_nrw_metrics.py` or the
bootstrap snapshot happens to have written, not the canonical PostGIS model in
`publish.nrw_ev_baseline_metrics`.  Contract C06 retires this path once the
canonical replacement is verified; that removal is owned by S21 (finding F43).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
SAMPLE_REGIONS = ROOT / "frontend" / "data" / "nrw_regions_sample.geojson"


st.set_page_config(page_title="NRW Energy Infrastructure Intelligence", layout="wide")
st.title("Geospatial EV Charging Infrastructure Intelligence Platform for NRW")


@st.cache_data
def load_regions() -> pd.DataFrame:
    processed = PROCESSED / "nrw_district_metrics.geojson"
    path = processed if processed.exists() else SAMPLE_REGIONS
    regions = gpd.read_file(path)
    return pd.DataFrame(regions.drop(columns="geometry", errors="ignore"))


def first_existing(columns: pd.Index, candidates: list[str]) -> str | None:
    return next((column for column in candidates if column in columns), None)


regions = load_regions()

metric_options = [
    column
    for column in [
        "investment_priority_score",
        "investmentPriorityScore",
        "charging_supply_score",
        "chargingSupplyScore",
        "charger_deficit_score",
        "chargerDeficitScore",
        "data_quality_score",
        "dataQualityScore",
        "chargers_total",
        "stationCount",
    ]
    if column in regions.columns
]

metric = st.selectbox("Ranking metric", metric_options)
name_col = first_existing(regions.columns, ["district_name", "name", "region_name"]) or regions.columns[0]
station_col = first_existing(regions.columns, ["chargers_total", "stationCount", "charging_stations"])

left, middle, right = st.columns(3)
left.metric("NRW NUTS-3 districts", len(regions))
middle.metric("Total stations", int(regions[station_col].sum()) if station_col else 0)
right.metric("Top district", regions.sort_values(metric, ascending=False).iloc[0][name_col])

st.caption("Current POC scores are based on charger supply. Population, roads, grid and renewable layers are still enrichment tasks.")

st.subheader("Top NRW districts")
st.dataframe(
    regions.sort_values(metric, ascending=False).head(20),
    use_container_width=True,
    hide_index=True,
)
