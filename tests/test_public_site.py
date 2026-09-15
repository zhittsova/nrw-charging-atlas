from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import public_site
from scripts.export_nrw_runtime import write_snapshot
from test_export_nrw_runtime import payload


def runtime(tmp_path: Path) -> Path:
    candidate = payload()
    candidate["metadata"]["score_model"] = [{"term": "test fixture"}]
    write_snapshot(candidate, tmp_path / "runtime")
    return tmp_path / "runtime/current"


def built_site(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<html lang="en" data-public-demo="true"></html>')
    return dist


def test_packages_only_verified_public_files(tmp_path: Path) -> None:
    source = runtime(tmp_path)
    (source / ".env").write_text("SECRET=not-public")
    (source / "private-backup.sql").write_text("not-public")
    dist = built_site(tmp_path)
    public_site.prepare(source, dist)
    assert set(path.name for path in (dist / "data").iterdir()) == set(public_site.FILES)
    assert not list(dist.rglob(".env"))
    assert (dist / "sources/attribution.txt").is_file()
    assert (dist / "404.html").is_file()


def test_corrupt_export_is_rejected_before_replacing_output(tmp_path: Path) -> None:
    source = runtime(tmp_path)
    dist = built_site(tmp_path)
    public_site.prepare(source, dist)
    previous = (dist / "data/nrw_regions_sample.geojson").read_bytes()
    (source / "nrw_regions_sample.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    with pytest.raises(ValueError, match="hash mismatch"):
        public_site.prepare(source, dist)
    assert (dist / "data/nrw_regions_sample.geojson").read_bytes() == previous


def test_manifest_cannot_redirect_the_copy_outside_snapshot(tmp_path: Path) -> None:
    source = runtime(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["districts"]["file"] = "../../.env"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unexpected artifact"):
        public_site.validate_snapshot(source)


@pytest.mark.parametrize("name", ["_worker.js", "functions/api.js", ".env", "assets/.secret"])
def test_refuses_functions_and_private_files(tmp_path: Path, name: str) -> None:
    source = runtime(tmp_path)
    dist = built_site(tmp_path)
    public_site.prepare(source, dist)
    unexpected = dist / name
    unexpected.parent.mkdir(exist_ok=True, parents=True)
    unexpected.write_text("must not publish")
    with pytest.raises(ValueError, match="Server function or private file"):
        public_site.check_site(dist)


def test_refuses_a_file_over_pages_limit(tmp_path: Path) -> None:
    source = runtime(tmp_path)
    dist = built_site(tmp_path)
    public_site.prepare(source, dist)
    with (dist / "large.geojson").open("wb") as stream:
        stream.truncate(public_site.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match="25 MiB"):
        public_site.check_site(dist)


def test_old_formula_requires_a_refresh(tmp_path: Path) -> None:
    source = runtime(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["formula_version"] = "old-model"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="formula version"):
        public_site.validate_snapshot(source)
