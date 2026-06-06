from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
SAMPLE_REGIONS = ROOT / "frontend" / "data" / "germany_regions_sample.geojson"


st.set_page_config(page_title="Germany Energy Infrastructure Intelligence", layout="wide")
st.title("Germany Energy Infrastructure Intelligence Platform")


@st.cache_data
def load_regions() -> pd.DataFrame:
    processed = PROCESSED / "nuts3_charger_metrics.geojson"
    path = processed if processed.exists() else SAMPLE_REGIONS
    regions = gpd.read_file(path)
    return pd.DataFrame(regions.drop(columns="geometry"))


regions = load_regions()

metric = st.selectbox(
    "Ranking metric",
    [column for column in regions.columns if column.endswith("Score") or column in {"charging_stations", "chargers_per_km2"}],
)

left, middle, right = st.columns(3)
left.metric("Regions", len(regions))
middle.metric("Total stations", int(regions.get("charging_stations", regions.get("stationCount", pd.Series([0]))).sum()))
right.metric("Top region", regions.sort_values(metric, ascending=False).iloc[0].get("region_name", regions.sort_values(metric, ascending=False).iloc[0].get("name")))

st.subheader("Top regions")
st.dataframe(
    regions.sort_values(metric, ascending=False).head(20),
    use_container_width=True,
    hide_index=True,
)
