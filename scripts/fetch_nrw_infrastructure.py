from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "catalog" / "nrw_infrastructure_sources.csv"
PROVENANCE_DIR = ROOT / "data" / "raw" / "provenance"
USER_AGENT = "nrw-energy-infrastructure-intelligence/0.1"
CHUNK_SIZE = 1024 * 1024


def read_manifest(path: Path = MANIFEST_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _resolve_target(root: Path, target_path: str) -> Path:
    root = root.resolve()
    target = (root / target_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Target path escapes project root: {target_path}")
    return target


def _write_json_atomically(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def fetch_source(
    row: dict[str, str],
    allow_large: bool = False,
    *,
    root: Path = ROOT,
    provenance_dir: Path | None = None,
    opener: Callable[..., BinaryIO] = urlopen,
) -> dict[str, object]:
    if row["access_type"] == "public_large" and not allow_large:
        raise PermissionError(f"{row['dataset_id']} requires --allow-large")

    max_bytes = int(row["max_bytes"])
    target = _resolve_target(root, row["target_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)

    request = Request(row["url"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    downloaded_bytes = 0
    content_type = ""
    try:
        with opener(request, timeout=300) as response:
            content_length_text = response.headers.get("Content-Length")
            if content_length_text and int(content_length_text) > max_bytes:
                raise ValueError(
                    f"{row['dataset_id']} content length {content_length_text} exceeds max_bytes {max_bytes}"
                )
            content_type = response.headers.get("Content-Type", "")
            with partial.open("wb") as file:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError(
                            f"{row['dataset_id']} streamed content exceeds max_bytes {max_bytes}"
                        )
                    digest.update(chunk)
                    file.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

    result: dict[str, object] = {
        "dataset_id": row["dataset_id"],
        "url": row["url"],
        "target_path": row["target_path"],
        "licence": row["licence"],
        "access_type": row["access_type"],
        "bytes": downloaded_bytes,
        "sha256": digest.hexdigest(),
        "content_type": content_type,
        "accessed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    provenance_root = provenance_dir or PROVENANCE_DIR
    _write_json_atomically(provenance_root / f"{row['dataset_id']}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", action="append", default=[], help="Fetch only the selected dataset id")
    parser.add_argument("--allow-large", action="store_true", help="Allow sources marked public_large")
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
        result = fetch_source(row, allow_large=args.allow_large)
        print(f"{row['dataset_id']}: downloaded {result['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
