from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import nrw_charger_quality as quality  # noqa: E402


def square_region(code: str, name: str, west: float, east: float) -> dict:
    return {
        "type": "Feature",
        "properties": {"nuts_code": code, "district_name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, 0.0],
                    [east, 0.0],
                    [east, 1.0],
                    [west, 1.0],
                    [west, 0.0],
                ]
            ],
        },
    }


def charger(
    station_id: str | None,
    longitude: object,
    latitude: object,
    *,
    district_text: str = "",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "id": station_id,
            "name": f"Station {station_id}",
            "operator": "Test operator",
            "district_text": district_text,
        },
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
    }


class ChargerClassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.regions = [
            square_region("DEA01", "West", 0.0, 1.0),
            square_region("DEA02", "East", 1.0, 2.0),
        ]

    def test_classifies_inside_boundary_outside_invalid_and_duplicate_records(self) -> None:
        candidates = [
            charger("inside", 0.5, 0.5),
            charger("boundary", 0.0, 0.5),
            charger("outside", 3.0, 0.5, district_text="West"),
            charger("invalid", math.nan, 0.5),
            charger("inside", 1.5, 0.5),
        ]

        accepted, rejected = quality.classify_chargers(candidates, self.regions)

        self.assertEqual(
            [item["properties"]["id"] for item in accepted],
            ["inside", "boundary"],
        )
        self.assertEqual(
            [item["properties"]["nuts_code"] for item in accepted],
            ["DEA01", "DEA01"],
        )
        self.assertEqual(
            [item["reason"] for item in rejected],
            ["outside_nrw", "invalid_coordinates", "duplicate_id"],
        )
        self.assertEqual(rejected[0]["district_text"], "West")
        self.assertEqual(tuple(rejected[0]), quality.EXCEPTION_FIELDS)

    def test_rejects_out_of_range_coordinates_as_invalid(self) -> None:
        accepted, rejected = quality.classify_chargers(
            [charger("bad-range", 181.0, 91.0)],
            self.regions,
        )

        self.assertEqual(accepted, [])
        self.assertEqual([item["reason"] for item in rejected], ["invalid_coordinates"])

    def test_missing_station_id_is_a_source_schema_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Charging record is missing id"):
            quality.classify_chargers([charger(None, 0.5, 0.5)], self.regions)


class ReconciliationTest(unittest.TestCase):
    def setUp(self) -> None:
        regions = [square_region("DEA01", "West", 0.0, 1.0)]
        self.accepted, self.rejected = quality.classify_chargers(
            [charger("inside", 0.5, 0.5), charger("outside", 2.0, 0.5)],
            regions,
        )

    def test_accepts_a_complete_partition_with_known_regions_and_reasons(self) -> None:
        quality.assert_reconciled(
            source_count=2,
            accepted=self.accepted,
            rejected=self.rejected,
            region_codes={"DEA01"},
        )

    def test_rejects_a_lost_source_record(self) -> None:
        with self.assertRaisesRegex(ValueError, "accepted plus rejected"):
            quality.assert_reconciled(3, self.accepted, self.rejected, {"DEA01"})

    def test_rejects_duplicate_accepted_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "Accepted charger ids are not unique"):
            quality.assert_reconciled(
                3,
                [*self.accepted, self.accepted[0]],
                self.rejected,
                {"DEA01"},
            )

    def test_rejects_unknown_exception_reason(self) -> None:
        bad_rejected = [{**self.rejected[0], "reason": "invented_reason"}]
        with self.assertRaisesRegex(ValueError, "Unknown charger exception reason"):
            quality.assert_reconciled(2, self.accepted, bad_rejected, {"DEA01"})

    def test_rejects_an_accepted_unknown_nuts_code(self) -> None:
        bad_accepted = [
            {
                **self.accepted[0],
                "properties": {
                    **self.accepted[0]["properties"],
                    "nuts_code": "DEZZZ",
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "unknown NRW district"):
            quality.assert_reconciled(2, bad_accepted, self.rejected, {"DEA01"})


if __name__ == "__main__":
    unittest.main()
