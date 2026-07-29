from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config_utils import ROOT
from load_nrw_postgis import _copy_block, read_admin_regions, run_psql


API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/demo_r_pjanaggr3"


def fetch_population(nuts_code: str) -> dict:
    query = urlencode({"lang": "en", "sex": "T", "age": "TOTAL", "unit": "NR", "geo": nuts_code})
    request = Request(f"{API}?{query}", headers={"User-Agent": "nrw-energy-infrastructure-intelligence/0.1"})
    with urlopen(request, timeout=60) as response:
        document = json.load(response)
    time_index = document["dimension"]["time"]["category"]["index"]
    values = document.get("value", {})
    candidates = [
        (int(year), int(values[str(position)]))
        for year, position in time_index.items()
        if str(position) in values
    ]
    if not candidates:
        raise ValueError(f"Eurostat returned no population for {nuts_code}")
    year, population = max(candidates)
    return {
        "district_code": nuts_code,
        "nuts_code": nuts_code,
        "ags": None,
        "population": population,
        "reference_year": year,
        "source": "Eurostat demo_r_pjanaggr3",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    regions = read_admin_regions(ROOT / "frontend/data/nrw_regions_sample.geojson")
    with ThreadPoolExecutor(max_workers=8) as executor:
        rows = list(executor.map(fetch_population, [str(region["nuts_code"]) for region in regions]))
    snapshot = {
        "dataset": "demo_r_pjanaggr3",
        "source_url": API,
        "records": rows,
    }
    target = ROOT / "data/raw/eurostat_population_nrw.json"
    target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    columns = ("district_code", "nuts_code", "ags", "population", "reference_year", "source")
    sql = f"""BEGIN;
CREATE TEMP TABLE import_population (
  district_code text, nuts_code text, ags text, population integer,
  reference_year integer, source text
) ON COMMIT DROP;
{_copy_block("import_population", columns, rows)}
TRUNCATE raw.population;
INSERT INTO raw.population
SELECT district_code, nuts_code, NULLIF(ags, ''), population, reference_year, source
FROM import_population;
REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;
COMMIT;
"""
    run_psql(args.database_url, [sql])
    print(f"loaded population for {len(rows)} districts; years {min(r['reference_year'] for r in rows)}-"
          f"{max(r['reference_year'] for r in rows)}")


if __name__ == "__main__":
    main()
