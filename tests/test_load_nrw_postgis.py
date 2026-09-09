from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import load_nrw_postgis as loader  # noqa: E402


def feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def region(code: str = "DEA01") -> dict:
    return {
        "type": "Feature",
        "properties": {
            "nuts_code": code,
            "ags": "05111000",
            "district_name": "Düsseldorf",
            "region": "Nordrhein-Westfalen",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[6.7, 51.1], [6.9, 51.1], [6.9, 51.3], [6.7, 51.1]]],
        },
    }


def charger(station_id: str = "station-1") -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": station_id,
            "operator": "Stadtwerke Düsseldorf",
            "status": "In Betrieb",
            "charger_type": "Schnellladeeinrichtung",
            "charging_points": 4,
            "power_kw": 150.0,
            "max_point_power_kw": 150.0,
            "district_text": "Düsseldorf",
            "state": "Nordrhein-Westfalen",
        },
        "geometry": {"type": "Point", "coordinates": [6.78, 51.23]},
    }


class SnapshotPreparationTest(unittest.TestCase):
    def test_reads_real_geojson_contract_into_raw_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions_path = root / "regions.geojson"
            chargers_path = root / "chargers.geojson"
            regions_path.write_text(json.dumps(feature_collection([region()])), encoding="utf-8")
            chargers_path.write_text(json.dumps(feature_collection([charger()])), encoding="utf-8")

            regions = loader.read_admin_regions(regions_path)
            chargers = loader.read_chargers(chargers_path)

        self.assertEqual(
            regions,
            [
                {
                    "nuts_code": "DEA01",
                    "ags": "05111000",
                    "district_name": "Düsseldorf",
                    "region_name": "Nordrhein-Westfalen",
                    "geom_json": json.dumps(region()["geometry"], ensure_ascii=False, separators=(",", ":")),
                }
            ],
        )
        self.assertEqual(chargers[0]["source_id"], "station-1")
        self.assertEqual(chargers[0]["operator"], "Stadtwerke Düsseldorf")
        self.assertEqual(chargers[0]["charging_points"], 4)
        self.assertEqual(chargers[0]["power_kw"], 150.0)
        self.assertEqual(chargers[0]["max_point_power_kw"], 150.0)
        self.assertEqual(
            chargers[0]["geom_json"],
            '{"type":"Point","coordinates":[6.78,51.23]}',
        )

    def test_rejects_duplicate_or_missing_snapshot_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate_regions = root / "duplicate-regions.geojson"
            missing_charger_id = root / "missing-charger-id.geojson"
            duplicate_regions.write_text(
                json.dumps(feature_collection([region(), region()])),
                encoding="utf-8",
            )
            broken = charger()
            broken["properties"]["id"] = None
            missing_charger_id.write_text(
                json.dumps(feature_collection([broken])),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate admin region key: DEA01"):
                loader.read_admin_regions(duplicate_regions)
            with self.assertRaisesRegex(ValueError, "Charging feature is missing id"):
                loader.read_chargers(missing_charger_id)

    def test_rejects_charger_coordinates_outside_wgs84_range(self) -> None:
        broken = charger()
        broken["geometry"]["coordinates"] = [181.0, 51.23]

        with self.assertRaisesRegex(ValueError, "station-1 has invalid Point coordinates"):
            loader.charger_row(broken)


class ImportScriptTest(unittest.TestCase):
    def test_refuses_an_empty_snapshot_before_destructive_sync(self) -> None:
        with self.assertRaisesRegex(ValueError, "Charging snapshot is empty"):
            loader.validate_snapshot([loader.admin_row(region())], [])

    def test_snapshot_import_is_transactional_and_removes_stale_rows(self) -> None:
        sql = loader.build_import_script([loader.admin_row(region())], [loader.charger_row(charger())])

        self.assertIn("BEGIN;", sql)
        self.assertIn("ON CONFLICT (nuts_code) DO UPDATE", sql)
        self.assertIn("ON CONFLICT (source_id) DO UPDATE", sql)
        self.assertIn("DELETE FROM raw.admin_regions AS existing", sql)
        self.assertIn("DELETE FROM raw.chargers AS existing", sql)
        self.assertIn("REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics", sql)
        self.assertIn("max_point_power_kw", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertIn("Stadtwerke Düsseldorf", sql)

    def test_psql_failure_is_reported_with_server_diagnostics(self) -> None:
        def failing_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 3, stdout="", stderr="relation raw.chargers does not exist")

        with self.assertRaisesRegex(RuntimeError, "relation raw.chargers does not exist"):
            loader.run_psql(
                "postgresql://example.invalid/nrw",
                ["SELECT 1;"],
                runner=failing_runner,
            )



class SourceSnapshotTest(unittest.TestCase):
    """The register's publication date is provenance, not a per-station field."""

    def _write(self, document: dict) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / "chargers.geojson"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_reads_the_snapshot_date_the_generator_recorded(self) -> None:
        path = self._write(
            {
                "type": "FeatureCollection",
                "source_key": "bnetza_ladesaeulenregister",
                "source_name": "Bundesnetzagentur Ladesaeulenregister",
                "snapshot_date": "2026-04-22",
                "features": [charger()],
            }
        )

        self.assertEqual(
            loader.read_source_snapshot(path),
            {
                "source_key": "bnetza_ladesaeulenregister",
                "snapshot_date": "2026-04-22",
                "source_name": "Bundesnetzagentur Ladesaeulenregister",
            },
        )

    def test_a_missing_snapshot_date_stays_unknown(self) -> None:
        path = self._write(
            {"type": "FeatureCollection", "snapshot_date": None, "features": [charger()]}
        )
        self.assertIsNone(loader.read_source_snapshot(path))

    def test_an_invalid_snapshot_date_is_rejected_rather_than_ignored(self) -> None:
        path = self._write(
            {"type": "FeatureCollection", "snapshot_date": "22.04.2026", "features": [charger()]}
        )
        with self.assertRaisesRegex(ValueError, "invalid snapshot_date"):
            loader.read_source_snapshot(path)

    def test_a_load_without_a_date_clears_the_previous_one(self) -> None:
        """A stale date attached to fresh data would be worse than none."""
        sql = loader.build_import_script(
            [loader.admin_row(region())],
            [loader.charger_row(charger())],
            source_snapshot=None,
        )

        self.assertIn("DELETE FROM raw.source_snapshots", sql)
        self.assertNotIn("INSERT INTO raw.source_snapshots", sql)

    def test_a_recorded_date_is_upserted_for_its_source_key(self) -> None:
        sql = loader.build_import_script(
            [loader.admin_row(region())],
            [loader.charger_row(charger())],
            source_snapshot={
                "source_key": "bnetza_ladesaeulenregister",
                "snapshot_date": "2026-04-22",
                "source_name": "Bundesnetzagentur Ladesaeulenregister",
            },
        )

        self.assertIn("INSERT INTO raw.source_snapshots", sql)
        self.assertIn("ON CONFLICT (source_key) DO UPDATE", sql)
        self.assertIn("2026-04-22", sql)

if __name__ == "__main__":
    unittest.main()
