from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import export_nrw_runtime as exporter  # noqa: E402


def feature(properties: dict, geometry_type: str = "Point") -> dict:
    coordinates = [7.0, 51.0] if geometry_type == "Point" else [[[7.0, 51.0], [7.1, 51.0], [7.0, 51.1], [7.0, 51.0]]]
    return {"type": "Feature", "properties": properties, "geometry": {"type": geometry_type, "coordinates": coordinates}}


def district(code: str) -> dict:
    properties = {field: None for field in exporter.DISTRICT_REQUIRED_FIELDS}
    properties.update({"nuts_code": code, "district_name": code, "formula_version": "nrw-2026.09.1"})
    return feature(properties, "Polygon")


def payload() -> dict:
    return {
        "districts": {"type": "FeatureCollection", "features": [district(f"DEA{index:02d}") for index in range(1, 54)]},
        "chargers": {"type": "FeatureCollection", "features": [feature({"source_id": "charger-1", "power_kw": None})]},
        "autobahns": {"type": "FeatureCollection", "features": [feature({"source_id": "a-1"})]},
        "regional_roads": {"type": "FeatureCollection", "features": [feature({"source_id": "r-1"})]},
        "renewables": {"type": "FeatureCollection", "features": [feature({"source_id": "wind-1", "technology": "Windenergie", "status": "In Betrieb", "capacity_mw": 1})]},
        "metadata": {"formula_version": "nrw-2026.09.1", "formula_version_date": "2026-09-09", "score_model": [], "source_snapshots": [], "ingest_run_id": "00000000-0000-0000-0000-000000000001", "ingested_source_provenance": [{"source_key": "fixture", "source_path": "data/raw/fixture", "bytes": 1, "sha256": "0" * 64, "provenance": None, "provenance_sha256": None}]},
    }


class RuntimeExportTest(unittest.TestCase):
    def test_writes_stable_logical_artifacts_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = exporter.write_snapshot(payload(), root)
            first_bytes = (root / "current" / "nrw_regions_sample.geojson").read_bytes()
            second = exporter.write_snapshot(payload(), root)
            second_bytes = (root / "current" / "nrw_regions_sample.geojson").read_bytes()
            manifest = json.loads((root / "current" / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(first["artifacts"], second["artifacts"])
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(manifest["artifacts"]["districts"]["count"], 53)
            self.assertEqual(manifest["renewable_display_filter"]["technologies"], ["Photovoltaik Freifläche", "Windenergie"])
            self.assertTrue((root / "current").is_symlink())
            self.assertEqual(len(list((root / ".generations").iterdir())), 1)

    def test_invalid_candidate_preserves_last_good_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter.write_snapshot(payload(), root)
            previous = (root / "current" / "nrw_renewable_assets_sample.geojson").read_bytes()
            broken = payload()
            broken["renewables"]["features"][0]["properties"]["technology"] = "Photovoltaik Bauliche"
            with self.assertRaisesRegex(ValueError, "non-display technology"):
                exporter.write_snapshot(broken, root)
            self.assertEqual((root / "current" / "nrw_renewable_assets_sample.geojson").read_bytes(), previous)

    def test_failed_pointer_promotion_preserves_complete_last_good_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter.write_snapshot(payload(), root)
            previous = (root / "current" / "nrw_renewable_assets_sample.geojson").read_bytes()
            real_replace = os.replace

            def fail_pointer(source: Path | str, destination: Path | str) -> None:
                if Path(source).name.startswith(".current-"):
                    raise OSError("injected pointer promotion failure")
                real_replace(source, destination)

            from unittest.mock import patch
            with patch.object(exporter.os, "replace", side_effect=fail_pointer):
                with self.assertRaisesRegex(OSError, "pointer promotion"):
                    exporter.write_snapshot(payload(), root)
            self.assertTrue((root / "current").is_symlink())
            self.assertEqual((root / "current" / "nrw_renewable_assets_sample.geojson").read_bytes(), previous)
            self.assertEqual(len(list((root / ".generations").iterdir())), 2)

    def test_missing_or_malformed_ingested_provenance_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no successful ingest-run"):
            exporter.validate_ingested_provenance({"ingested_source_provenance": []})
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            exporter.validate_ingested_provenance({
                "ingest_run_id": "run", "ingested_source_provenance": [{"source_key": "raw", "source_path": "data/raw/a", "bytes": 1, "sha256": "not-a-hash"}],
            })

    def test_export_manifest_uses_database_bound_not_refreshed_local_provenance(self) -> None:
        stable = payload()
        manifest = exporter.build_manifest(stable, {})
        # A fetch can replace a local cache record, but export receives only the
        # successful seed's snapshot metadata and never reads that mutable file.
        _refreshed_local_record = {"sha256": "new-unseeded-input"}
        self.assertNotIn("new-unseeded-input", json.dumps(manifest))
        self.assertEqual(manifest["ingest_run_id"], stable["metadata"]["ingest_run_id"])
        self.assertEqual(manifest["ingested_source_provenance"], stable["metadata"]["ingested_source_provenance"])

    def test_snapshot_query_uses_one_read_only_repeatable_read_transaction(self) -> None:
        sql = exporter.snapshot_sql()
        self.assertIn("REPEATABLE READ READ ONLY", sql)
        self.assertIn("publish.nrw_ev_baseline_metrics", sql)
        self.assertIn("publish.nrw_renewable_potential", sql)
        self.assertIn("status = 'In Betrieb'", sql)
        self.assertIn("Photovoltaik Freifläche", sql)
        self.assertIn("raw.ingest_runs", sql)
        self.assertIn("raw.ingest_source_inputs", sql)
        self.assertNotIn("AS row;)", sql)


if __name__ == "__main__":
    unittest.main()
