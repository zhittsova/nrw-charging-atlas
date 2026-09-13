from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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


class ChargingPowerParsingTest(unittest.TestCase):
    def test_max_point_power_uses_largest_finite_positive_connector_value(self) -> None:
        row = {
            "Nennleistung Stecker1": "22",
            "Nennleistung Stecker2": "50,0",
            "Nennleistung Stecker3": "invalid",
            "Nennleistung Stecker4": "NaN",
            "Nennleistung Stecker5": "0",
            "Nennleistung Stecker6": "-11",
        }

        self.assertEqual(generator.max_point_power_kw(row), 50.0)

    def test_max_point_power_is_unknown_when_all_connector_values_are_invalid(self) -> None:
        row = {
            "Nennleistung Stecker1": "",
            "Nennleistung Stecker2": "NaN",
            "Nennleistung Stecker3": "Infinity",
            "Nennleistung Stecker4": "-1",
        }

        self.assertIsNone(generator.max_point_power_kw(row))

    def test_load_fixture_preserves_aggregate_and_derived_maximum_independently(self) -> None:
        csv_text = "\n".join(
            [
                *(["header"] * 7),
                "Letzte Aktualisierung vom: 22.04.2026;;;",
                "header",
                "header",
                ";".join(
                    [
                        "Bundesland",
                        "Längengrad",
                        "Breitengrad",
                        "Nennleistung Ladeeinrichtung [kW]",
                        "Anzahl Ladepunkte",
                        "Nennleistung Stecker1",
                        "Nennleistung Stecker2",
                        "Nennleistung Stecker3",
                        "Ladeeinrichtungs-ID",
                        "Betreiber",
                        "Status",
                        "Art der Ladeeinrichtung",
                    ]
                ),
                ";".join(
                    [
                        "Nordrhein-Westfalen",
                        "7,1",
                        "51,2",
                        "44",
                        "2",
                        "22",
                        "22",
                        "",
                        "fixture-22x2",
                        "Operator",
                        "In Betrieb",
                        "Schnellladeeinrichtung",
                    ]
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.csv"
            source.write_text(csv_text, encoding="cp1252")
            with patch.object(generator, "ROOT", root):
                chargers, snapshot_date = generator.load_nrw_chargers(
                    {"raw_bnetza_path": "fixture.csv", "region_name": "Nordrhein-Westfalen"}
                )

        self.assertEqual(len(chargers), 1)
        properties = chargers[0]["properties"]
        self.assertEqual(properties["power_kw"], 44.0)
        self.assertEqual(properties["max_point_power_kw"], 22.0)
        self.assertEqual(properties["charging_points"], 2)
        # The register's own publication date is the only provenance the file
        # carries, and it is read from the preamble rather than invented.
        self.assertEqual(snapshot_date, "2026-04-22")

    def test_snapshot_date_is_read_from_the_register_preamble(self) -> None:
        self.assertEqual(
            generator.parse_charger_snapshot_date(
                "Ladesaeulenregister\nLetzte Aktualisierung vom: 22.04.2026;;;\n"
            ),
            "2026-04-22",
        )
        self.assertEqual(
            generator.parse_charger_snapshot_date("letzte  aktualisierung  vom:31.12.2025"),
            "2025-12-31",
        )

    def test_an_unreadable_snapshot_date_is_unknown_rather_than_substituted(self) -> None:
        for preamble in (
            "Ladesaeulenregister Bundesnetzagentur",
            "Letzte Aktualisierung vom: 31.02.2026",
            "Letzte Aktualisierung vom: 2026-04-22",
        ):
            with self.subTest(preamble):
                self.assertIsNone(generator.parse_charger_snapshot_date(preamble))

    def test_threshold_is_exactly_50_and_unknown_is_not_normal(self) -> None:
        self.assertTrue(generator.is_fast_power(50.0))
        self.assertFalse(generator.is_fast_power(49.999))
        self.assertIsNone(generator.classify_power(None))
        self.assertEqual(generator.classify_power(22.0), "normal")
        self.assertEqual(generator.classify_power(50.0), "fast")


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

    def test_generated_chargers_carry_the_source_snapshot_date(self) -> None:
        collection = generator.charger_collection([accepted_charger("a", "DEA01")], "2026-04-22")
        self.assertEqual(collection["source_key"], "bnetza_ladesaeulenregister")
        self.assertEqual(collection["snapshot_date"], "2026-04-22")

    def test_an_unknown_snapshot_date_is_written_as_null_rather_than_omitted(self) -> None:
        collection = generator.charger_collection([accepted_charger("a", "DEA01")], None)
        self.assertIn("snapshot_date", collection)
        self.assertIsNone(collection["snapshot_date"])

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
