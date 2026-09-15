"""Package and recover a static-only public site using verified canonical exports.

No raw inputs, Docker configuration, credentials or server functions are copied.
Uses only the standard library so a frontend deployment need not install GIS tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.export_nrw_runtime import ARTIFACTS, validate_collection, validate_ingested_provenance

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 20_000
MANIFEST = "manifest.json"
FILES = [MANIFEST, *(item[0] for item in ARTIFACTS.values())]


def validate_snapshot(runtime: Path) -> dict:
    manifest = json.loads((runtime / MANIFEST).read_text())
    validate_ingested_provenance(manifest)
    formula = re.search(r"'([^']+)'::text AS formula_version", (ROOT / "db/nrw_analytics.sql").read_text())
    if not formula or manifest.get("formula_version") != formula[1]:
        raise ValueError("Snapshot formula version differs from this checkout; run a data refresh")
    if not manifest.get("generated_at") or not manifest.get("score_model"):
        raise ValueError("Snapshot must contain its export date and published score model")
    if set(manifest.get("artifacts", {})) != set(ARTIFACTS):
        raise ValueError("Snapshot must contain exactly the five canonical layers")
    for name, (filename, _, _) in ARTIFACTS.items():
        record = manifest["artifacts"][name]
        if record.get("file") != filename:
            raise ValueError(f"Unexpected artifact filename for {name}")
        path = runtime / filename
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{filename} exceeds the Pages 25 MiB file limit")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record.get("sha256"):
            raise ValueError(f"Snapshot hash mismatch: {filename}")
        collection = json.loads(content)
        validate_collection(name, collection)
        if len(collection["features"]) != record.get("count"):
            raise ValueError(f"Snapshot count mismatch: {filename}")
    return manifest


def check_site(dist: Path) -> None:
    # Wrangler can discover Functions/configuration beside dist, too.
    for directory in (ROOT, ROOT / "frontend"):
        for name in ("functions", "wrangler.toml", "wrangler.json", "wrangler.jsonc"):
            if (directory / name).exists():
                raise ValueError(f"Unexpected server/deployment configuration: {directory / name}")
    files = list(dist.rglob("*"))
    if sum(path.is_file() for path in files) > MAX_FILES:
        raise ValueError("Public site exceeds the Pages file-count limit")
    for path in files:
        if path.is_symlink():
            raise ValueError(f"Symlink in public site: {path}")
        relative = path.relative_to(dist)
        if any(part.startswith(".") or part == "functions" or part.startswith("_worker") for part in relative.parts):
            raise ValueError(f"Server function or private file in public site: {relative}")
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{relative} exceeds the Pages 25 MiB file limit")
    shell = (dist / "index.html").read_text()
    if 'data-public-demo="true"' not in shell or "localhost" in shell:
        raise ValueError("Build must use npm run build:public and contain no localhost links")
    if not (dist / "404.html").is_file():
        raise ValueError("Public site needs an explicit 404 page, not an SPA fallback for missing data")
    validate_snapshot(dist / "data")


def prepare(runtime: Path, dist: Path) -> None:
    validate_snapshot(runtime)
    if not (dist / "index.html").is_file():
        raise ValueError("Build the public frontend before packaging data")
    target = dist / "data"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    for filename in FILES:
        shutil.copyfile(runtime / filename, target / filename)
    sources = dist / "sources"
    sources.mkdir(exist_ok=True)
    for filename in ("data_sources.csv", "nrw_infrastructure_sources.csv"):
        shutil.copyfile(ROOT / "catalog" / filename, sources / filename)
    shutil.copyfile(ROOT / "docs/data-attribution.md", sources / "attribution.txt")
    shutil.copyfile(ROOT / "NOTICE.md", sources / "software-notices.txt")
    shell_path = dist / "index.html"
    shell = shell_path.read_text()
    shell = shell.replace(
        'Available download and provenance records can be inspected in the source catalogs and the successful-ingest <code>data/runtime/current/manifest.json</code>.',
        '<a href="sources/attribution.txt">Data licences and attribution</a> · '
        '<a href="sources/software-notices.txt">Software notices</a> · '
        '<a href="sources/data_sources.csv">Source catalogue</a> · '
        '<a href="sources/nrw_infrastructure_sources.csv">Infrastructure sources</a> · '
        '<a href="data/manifest.json">Data provenance and export date</a>.'
    )
    shell_path.write_text(shell)
    (dist / "404.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Not found</title><h1>Not found</h1><p><a href="/">Return to the NRW Charging Atlas</a></p></html>\n')
    (dist / "_headers").write_text(
        "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  X-Frame-Options: DENY\n"
        "/\n  Cache-Control: no-cache\n"
        "/index.html\n  Cache-Control: no-cache\n"
        "/data/*\n  Content-Type: application/json; charset=utf-8\n  Cache-Control: public, max-age=0, must-revalidate\n"
        "/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n"
    )
    check_site(dist)
    print(f"Verified static-only site: {dist}")


def fetch_snapshot(base_url: str, target: Path) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("Snapshot origin must be an HTTPS URL without query or fragment")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        staged = Path(directory)
        for filename in FILES:
            # Cloudflare can reject urllib's generic Python User-Agent (1010).
            request = Request(
                f"{base_url.rstrip('/')}/data/{filename}",
                headers={
                    "User-Agent": "nrw-charging-atlas/1.0 (+https://github.com/zhittsova/nrw-charging-atlas)",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=90) as response:
                content = response.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(f"Download exceeds the Pages file limit: {filename}")
            (staged / filename).write_bytes(content)
        validate_snapshot(staged)
        target.mkdir(exist_ok=True)
        for filename in FILES:
            shutil.copyfile(staged / filename, target / filename)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "fetch"))
    parser.add_argument("--runtime", type=Path, default=ROOT / "data/runtime/current")
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend/dist")
    parser.add_argument("--origin")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.runtime, args.dist)
    elif args.action == "check":
        check_site(args.dist)
    else:
        if not args.origin:
            parser.error("fetch requires --origin")
        fetch_snapshot(args.origin, args.runtime)


if __name__ == "__main__":
    main()
