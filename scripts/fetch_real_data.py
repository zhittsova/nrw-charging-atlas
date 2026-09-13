from __future__ import annotations

import argparse
import csv
import re
import socket
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from source_cache import fetch_source as fetch_cached_source, reuse_cached_source, validate_consumed_format


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog" / "data_sources.csv"
PROVENANCE_DIR = ROOT / "data" / "raw" / "provenance"


def read_catalog(path: Path = CATALOG_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "energy-infra-intelligence-poc/0.1"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def discover_bnetza_csv(landing_page: str) -> str:
    html = request_bytes(landing_page).decode("utf-8", errors="ignore")
    matches = re.findall(r'https://data\.bundesnetzagentur\.de/[^"\']+Ladesaeulenregister_BNetzA_[^"\']+?\.csv', html)
    if not matches:
        matches = re.findall(r'/[^"\']+Ladesaeulenregister_BNetzA_[^"\']+?\.csv', html)
    if not matches:
        raise RuntimeError("Could not discover BNetzA charging register CSV from landing page")
    url = matches[0]
    if url.startswith("/"):
        url = f"https://www.bundesnetzagentur.de{url}"
    return url.replace("&amp;", "&")


def should_fetch(row: dict[str, str], include_large: bool) -> bool:
    return row["access_type"] in {"public", "public_large"} and (
        row["access_type"] != "public_large" or include_large
    )


def fetch_row(row: dict[str, str], *, include_large: bool, refresh: bool) -> tuple[str, str]:
    strategy = row["fetch_strategy"]
    if strategy == "eurostat_population_snapshot":
        from config_utils import read_simple_region_config
        from nrw_raw_inputs import load_nrw_regions
        from load_nrw_population_postgis import fetch_population_snapshot

        config = read_simple_region_config(ROOT / "config" / "regions" / "nrw.yml")
        codes = {str(feature["properties"]["nuts_code"]) for feature in load_nrw_regions(config)}
        result = fetch_population_snapshot(codes, refresh=refresh)
        return row["dataset_id"], f"{result['cache_status']}: {result['bytes']} bytes"
    if strategy not in {"discover_bnetza_csv", "direct_download", "direct_download_large"}:
        return row["dataset_id"], "not fetched (contextual/manual source)"
    cache_row = row | {"url": row["source_url"]}
    # A verified BNetzA input is reusable without touching its mutable landing
    # page. Discovery is only needed when a transfer is actually required.
    if strategy == "discover_bnetza_csv" and not refresh:
        cached = reuse_cached_source(
            cache_row,
            root=ROOT,
            provenance_dir=PROVENANCE_DIR,
            validator=lambda candidate: validate_consumed_format(candidate, dataset_id=row["dataset_id"]),
        )
        if cached:
            return row["dataset_id"], f"{cached['cache_status']}: {cached['bytes']} bytes"
    url = discover_bnetza_csv(row["source_url"]) if strategy == "discover_bnetza_csv" else row["source_url"]
    source_date_from_file = None
    if strategy == "discover_bnetza_csv":
        from nrw_raw_inputs import CHARGER_PREAMBLE_ROWS, parse_charger_snapshot_date

        def source_date_from_file(path: Path) -> str | None:
            with path.open(encoding="cp1252", errors="replace") as source:
                return parse_charger_snapshot_date("".join(next(source) for _ in range(CHARGER_PREAMBLE_ROWS)))

    result = fetch_cached_source(
        cache_row,
        root=ROOT,
        provenance_dir=PROVENANCE_DIR,
        refresh=refresh,
        allow_large=include_large,
        resolved_url=url,
        validator=lambda candidate: validate_consumed_format(candidate, dataset_id=row["dataset_id"]),
        source_date_from_file=source_date_from_file,
    )
    return row["dataset_id"], f"{result['cache_status']}: {result['bytes']} bytes"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-large", action="store_true", help="Fetch large OSM and OPSD renewable files too")
    parser.add_argument("--refresh", action="store_true", help="Explicitly replace validated cached inputs")
    parser.add_argument("--dataset", action="append", default=[], help="Fetch only selected dataset id")
    args = parser.parse_args()

    rows = read_catalog()
    selected_ids = set(args.dataset)
    if selected_ids:
        known = {row["dataset_id"] for row in rows}
        unknown = selected_ids - known
        if unknown:
            parser.error(f"Unknown dataset id(s): {', '.join(sorted(unknown))}")
        rows = [row for row in rows if row["dataset_id"] in selected_ids]
    selected = [row for row in rows if should_fetch(row, args.include_large)]

    if not selected:
        print("No datasets selected")
        return 0

    failures: list[str] = []
    for row in selected:
        try:
            dataset_id, message = fetch_row(row, include_large=args.include_large, refresh=args.refresh)
            print(f"{dataset_id}: {message}")
        except (TimeoutError, RuntimeError, socket.timeout, OSError, ValueError) as error:
            failures.append(f"{row['dataset_id']}: {error}")
            print(f"{row['dataset_id']}: failed: {error}", file=sys.stderr)

    if failures:
        print("\nFailures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
