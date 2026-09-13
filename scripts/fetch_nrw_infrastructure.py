from __future__ import annotations

import argparse
import csv
from pathlib import Path

from source_cache import fetch_source as fetch_cached_source, validate_consumed_format


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "catalog" / "nrw_infrastructure_sources.csv"
PROVENANCE_DIR = ROOT / "data" / "raw" / "provenance"


def read_manifest(path: Path = MANIFEST_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def fetch_source(
    row: dict[str, str],
    allow_large: bool = False,
    *,
    root: Path = ROOT,
    provenance_dir: Path | None = None,
    refresh: bool = False,
    opener: object | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if opener is not None:
        kwargs["opener"] = opener
    return fetch_cached_source(
        row,
        root=root,
        provenance_dir=provenance_dir or PROVENANCE_DIR,
        refresh=refresh,
        allow_large=allow_large,
        validator=lambda candidate: validate_consumed_format(candidate, dataset_id=row["dataset_id"]),
        **kwargs,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", action="append", default=[], help="Fetch only the selected dataset id")
    parser.add_argument("--allow-large", action="store_true", help="Allow sources marked public_large")
    parser.add_argument("--refresh", action="store_true", help="Replace validated cached inputs explicitly")
    args = parser.parse_args()

    rows = read_manifest()
    selected_ids = set(args.dataset)
    if selected_ids:
        known_ids = {row["dataset_id"] for row in rows}
        unknown = selected_ids - known_ids
        if unknown:
            parser.error(f"Unknown dataset id(s): {', '.join(sorted(unknown))}")
        rows = [row for row in rows if row["dataset_id"] in selected_ids]

    for row in rows:
        if row["access_type"] == "public_large" and not args.allow_large:
            print(f"{row['dataset_id']}: skipped (use --allow-large)")
            continue
        result = fetch_source(row, allow_large=args.allow_large, refresh=args.refresh)
        print(f"{row['dataset_id']}: {result['cache_status']} {result['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
