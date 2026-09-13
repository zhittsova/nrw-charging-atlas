from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import generate_nrw_frontend_data as generator  # noqa: E402
import nrw_charger_quality as quality  # noqa: E402


def region(code: str, chargers_total: int) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "nuts_code": code,
            "district_name": code,
            "chargers_total": chargers_total,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }


def accepted_charger(station_id: str, code: str) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": station_id,
            "nuts_code": code,
            "district_name": code,
        },
        "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
    }


class GeneratedOutputTest(unittest.TestCase):
    def test_writes_valid_geojson_and_exact_exception_columns(self) -> None:
        regions = [region("DEA01", 1)]
        accepted = [accepted_charger("accepted", "DEA01")]
        rejected = [
            {
                "id": "outside",
                "name": "Outside",
                "operator": "Operator",
                "district_text": "Kreis Düren",
                "longitude": 13.4,
                "latitude": 52.5,
                "reason": "outside_nrw",
            }
        ]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            region_path = output / "regions.geojson"
            charger_path = output / "chargers.geojson"
            exception_path = output / "exceptions.csv"

            generator.write_outputs_atomically(
                regions,
                accepted,
                rejected,
                region_path=region_path,
                charger_path=charger_path,
                exception_path=exception_path,
            )

            charger_output = json.loads(charger_path.read_text(encoding="utf-8"))
            exception_bytes = exception_path.read_bytes()
            with exception_path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)

        self.assertEqual([item["properties"]["id"] for item in charger_output["features"]], ["accepted"])
        self.assertNotIn(b"\r\n", exception_bytes)
        self.assertEqual(reader.fieldnames, list(quality.EXCEPTION_FIELDS))
        self.assertEqual(rows[0]["reason"], "outside_nrw")

    def test_district_totals_must_equal_accepted_feature_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "District station totals"):
            generator.assert_district_totals(
                [region("DEA01", 2)],
                [accepted_charger("accepted", "DEA01")],
            )

    def test_failed_validation_does_not_replace_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = [
                output / "regions.geojson",
                output / "chargers.geojson",
                output / "exceptions.csv",
            ]
            for path in paths:
                path.write_text("original", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "District station totals"):
                generator.generate_validated_outputs(
                    [region("DEA01", 2)],
                    [accepted_charger("accepted", "DEA01")],
                    [],
                    source_count=1,
                    region_path=paths[0],
                    charger_path=paths[1],
                    exception_path=paths[2],
                )

            self.assertEqual([path.read_text(encoding="utf-8") for path in paths], ["original"] * 3)


if __name__ == "__main__":
    unittest.main()
