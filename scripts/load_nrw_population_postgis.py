from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config_utils import ROOT
from load_nrw_postgis import _copy_block, read_raw_seed_snapshot


API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/demo_r_pjanaggr3"


def read_population_snapshot(path: Path, *, expected_codes: set[str]) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Population snapshot must contain a non-empty records array")
    rows: list[dict] = []
    seen: set[str] = set()
    for record in records:
        code = str(record.get("nuts_code") or "")
        population = record.get("population")
        reference_year = record.get("reference_year")
        if code in seen:
            raise ValueError(f"Population snapshot contains duplicate NUTS code: {code}")
        if code not in expected_codes:
            raise ValueError(f"Population snapshot contains unexpected NUTS code: {code}")
        if not isinstance(population, int) or population <= 0:
            raise ValueError(f"Population must be a positive integer for {code}")
        if not isinstance(reference_year, int) or reference_year <= 0:
            raise ValueError(f"Population reference year is invalid for {code}")
        seen.add(code)
        rows.append(
            {
                "district_code": record.get("district_code") or code,
                "nuts_code": code,
                "ags": record.get("ags"),
                "population": population,
                "reference_year": reference_year,
                "source": record.get("source") or "Eurostat demo_r_pjanaggr3",
            }
        )
    missing = sorted(expected_codes - seen)
    if missing:
        raise ValueError(f"Population snapshot is missing NUTS codes: {', '.join(missing)}")
    return rows


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
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--snapshot", type=Path, help="Use a checked population snapshot without API calls")
    args = parser.parse_args()
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required")
    regions, _, _ = read_raw_seed_snapshot()
    expected_codes = {str(region["nuts_code"]) for region in regions}
    if args.snapshot:
        rows = read_population_snapshot(args.snapshot, expected_codes=expected_codes)
    else:
        with ThreadPoolExecutor(max_workers=8) as executor:
            rows = list(executor.map(fetch_population, sorted(expected_codes)))
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
COMMIT;
"""
    raise ValueError(
        "Individual loaders only validate inputs. Use scripts/refresh_nrw_database.py "
        "to publish a complete atomic seed."
    )


if __name__ == "__main__":
    main()
