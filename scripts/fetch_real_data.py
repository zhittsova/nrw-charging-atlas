from __future__ import annotations

import argparse
import csv
import re
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalog" / "data_sources.csv"


def read_catalog(path: Path = CATALOG_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "energy-infra-intelligence-poc/0.1"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def save_url(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "energy-infra-intelligence-poc/0.1"})
    with urlopen(request, timeout=300) as response:
        with target.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)


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


def should_fetch(row: dict[str, str], include_large: bool, include_auth: bool) -> bool:
    access = row["access_type"]
    if access == "auth_required":
        return include_auth
    if access == "public_large":
        return include_large
    if access.startswith("manual"):
        return False
    return True


def fetch_row(row: dict[str, str]) -> tuple[str, str]:
    target = ROOT / row["target_path"]
    strategy = row["fetch_strategy"]

    if strategy == "discover_bnetza_csv":
        url = discover_bnetza_csv(row["source_url"])
        save_url(url, target)
        return row["dataset_id"], f"downloaded discovered file: {url}"

    if strategy in {"direct_download", "direct_download_large"}:
        save_url(row["source_url"], target)
        return row["dataset_id"], f"downloaded: {target}"

    if strategy in {"destatis_api", "regionalstatistik_api"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        token_note = {
            "dataset_id": row["dataset_id"],
            "status": "waiting_for_credentials",
            "source_url": row["source_url"],
            "note": "Add credentials to .env, then replace this placeholder with the API request result.",
        }
        target.write_text(str(token_note) + "\n", encoding="utf-8")
        return row["dataset_id"], "wrote auth placeholder"

    return row["dataset_id"], "skipped"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-large", action="store_true", help="Fetch large OSM and OPSD renewable files too")
    parser.add_argument("--include-auth", action="store_true", help="Create placeholders for auth based API sources")
    args = parser.parse_args()

    rows = read_catalog()
    selected = [row for row in rows if should_fetch(row, args.include_large, args.include_auth)]

    if not selected:
        print("No datasets selected")
        return 0

    failures: list[str] = []
    for row in selected:
        try:
            dataset_id, message = fetch_row(row)
            print(f"{dataset_id}: {message}")
        except (HTTPError, URLError, TimeoutError, RuntimeError, socket.timeout, OSError) as error:
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
