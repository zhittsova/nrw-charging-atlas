from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import load_nrw_population_postgis as loader  # noqa: E402


class PopulationSnapshotTest(unittest.TestCase):
    def test_reads_repeatable_population_snapshot(self) -> None:
        document = {
            "dataset": "demo_r_pjanaggr3",
            "records": [
                {
                    "district_code": "DEA01",
                    "nuts_code": "DEA01",
                    "ags": None,
                    "population": 100000,
                    "reference_year": 2024,
                    "source": "Eurostat",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "population.json"
            path.write_text(json.dumps(document), encoding="utf-8")

            rows = loader.read_population_snapshot(path, expected_codes={"DEA01"})

        self.assertEqual(rows[0]["population"], 100000)

    def test_rejects_missing_duplicate_or_non_positive_records(self) -> None:
        invalid_sets = [
            [],
            [
                {"nuts_code": "DEA01", "population": 1, "reference_year": 2024},
                {"nuts_code": "DEA01", "population": 2, "reference_year": 2024},
            ],
            [{"nuts_code": "DEA01", "population": 0, "reference_year": 2024}],
        ]
        for records in invalid_sets:
            with self.subTest(records=records), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "population.json"
                path.write_text(json.dumps({"records": records}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    loader.read_population_snapshot(path, expected_codes={"DEA01"})


if __name__ == "__main__":
    unittest.main()
