from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import nrw_raw_inputs as generator  # noqa: E402
import source_cache  # noqa: E402


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

    def test_loads_utf8_bom_register_with_current_coordinate_header(self) -> None:
        csv_text = "\n".join(
            [
                *("header" for _ in range(7)),
                "Letzte Aktualisierung vom: 01.09.2026;;;",
                "header",
                "header",
                ";".join(
                    [
                        "Ladeeinrichtungs-ID",
                        "Bundesland",
                        "Breitengrad",
                        "Längengrad",
                        "Nennleistung Ladeeinrichtung [kW]",
                        "Anzahl Ladepunkte",
                    ]
                ),
                ";".join(["fixture-utf8", "Nordrhein-Westfalen", "51,2", "7,1", "44", "2"]),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.csv"
            source.write_text(csv_text, encoding="utf-8-sig")
            source_cache.validate_consumed_format(source, dataset_id="bnetza_charging_register_nrw")
            with patch.object(generator, "ROOT", root):
                chargers, snapshot_date = generator.load_nrw_chargers(
                    {"raw_bnetza_path": "fixture.csv", "region_name": "Nordrhein-Westfalen"}
                )

        self.assertEqual(len(chargers), 1)
        self.assertEqual(chargers[0]["geometry"]["coordinates"], [7.1, 51.2])
        self.assertEqual(snapshot_date, "2026-09-01")

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


if __name__ == "__main__":
    unittest.main()
