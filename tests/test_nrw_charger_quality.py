from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))

from geometry_cases import GEOMETRY_CASES, case, invalid_cases, valid_cases  # noqa: E402

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


class ContainmentSemanticsTest(unittest.TestCase):
    """Ingestion containment must match the database's ST_Covers behaviour.

    The expectations below were confirmed against PostGIS 3.5 directly: a point
    in a hole is not covered, a point on a hole edge or an exterior edge is, and
    a point on a shared district boundary is covered by both neighbours.
    """

    def setUp(self) -> None:
        self.regions = [
            {
                "type": "Feature",
                "properties": {"nuts_code": "DEA01", "district_name": "West"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
                        [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]],
                    ],
                },
            },
            square_region("DEA02", "East", 1.0, 2.0),
        ]

    def test_a_point_in_a_hole_is_outside_the_district(self) -> None:
        accepted, rejected = quality.classify_chargers(
            [charger("in-hole", 0.5, 0.5)], self.regions
        )

        self.assertEqual(accepted, [])
        self.assertEqual([item["reason"] for item in rejected], ["outside_nrw"])

    def test_points_on_a_hole_edge_or_corner_stay_inside_the_district(self) -> None:
        accepted, rejected = quality.classify_chargers(
            [charger("hole-edge", 0.4, 0.5), charger("hole-corner", 0.4, 0.4)],
            self.regions,
        )

        self.assertEqual(rejected, [])
        self.assertEqual(
            [item["properties"]["nuts_code"] for item in accepted], ["DEA01", "DEA01"]
        )

    def test_a_shared_boundary_point_resolves_to_the_lowest_nuts_code(self) -> None:
        candidate = charger("shared", 1.0, 0.2)

        accepted, rejected = quality.classify_chargers([candidate], self.regions)
        reversed_accepted, _ = quality.classify_chargers(
            [candidate], list(reversed(self.regions))
        )

        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]["properties"]["nuts_code"], "DEA01")
        # District order must not decide which neighbour claims the station.
        self.assertEqual(reversed_accepted[0]["properties"]["nuts_code"], "DEA01")

    def test_a_shared_boundary_station_is_counted_once(self) -> None:
        accepted, _ = quality.classify_chargers(
            [charger("shared", 1.0, 0.2), charger("inside", 0.2, 0.2)], self.regions
        )

        self.assertEqual(len(accepted), 2)
        quality.assert_reconciled(2, accepted, [], {"DEA01", "DEA02"})


class DistrictGeometryValidationTest(unittest.TestCase):
    def region_with_exterior(self, ring: list) -> list[dict]:
        return [
            {
                "type": "Feature",
                "properties": {"nuts_code": "DEA01", "district_name": "West"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ]

    def test_an_unclosed_ring_is_an_ingestion_error(self) -> None:
        regions = self.region_with_exterior(
            [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        )
        with self.assertRaisesRegex(ValueError, "unclosed exterior ring"):
            quality.classify_chargers([charger("any", 0.5, 0.5)], regions)

    def test_a_degenerate_ring_is_an_ingestion_error(self) -> None:
        regions = self.region_with_exterior([[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]])
        with self.assertRaisesRegex(ValueError, "fewer than four positions"):
            quality.classify_chargers([charger("any", 0.5, 0.5)], regions)

    def test_a_non_finite_ring_position_is_an_ingestion_error(self) -> None:
        regions = self.region_with_exterior(
            [[0.0, 0.0], [math.inf, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        )
        with self.assertRaisesRegex(ValueError, "non-finite exterior ring position"):
            quality.classify_chargers([charger("any", 0.5, 0.5)], regions)

    def test_an_unclosed_hole_is_an_ingestion_error(self) -> None:
        regions = [
            {
                "type": "Feature",
                "properties": {"nuts_code": "DEA01", "district_name": "West"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
                        [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
                    ],
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "unclosed interior ring"):
            quality.classify_chargers([charger("any", 0.2, 0.2)], regions)

    def test_an_unsupported_geometry_type_is_named(self) -> None:
        regions = [
            {
                "type": "Feature",
                "properties": {"nuts_code": "DEA01", "district_name": "West"},
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            }
        ]
        with self.assertRaisesRegex(ValueError, "Unsupported district geometry: LineString"):
            quality.classify_chargers([charger("any", 0.5, 0.5)], regions)


class PolygonTopologyValidationTest(unittest.TestCase):
    """Structural checks cannot see topology; the GEOS verdict decides.

    Expected verdicts live in `tests/fixtures/geometry_cases.py` and are proved
    against GEOS in `tests/test_geometry_cases.py`, so these tests compare the
    ingestion validator with an independent engine rather than with itself.
    """

    def district(self, geometry: dict, code: str = "DEA01") -> dict:
        return {
            "type": "Feature",
            "properties": {"nuts_code": code, "district_name": "Fixture"},
            "geometry": geometry,
        }

    def test_every_invalid_topology_is_rejected_with_district_and_reason(self) -> None:
        for geometry_case in invalid_cases():
            with self.subTest(case=geometry_case.name):
                with self.assertRaises(ValueError) as raised:
                    quality.classify_chargers(
                        [charger("any", 0.5, 0.5)],
                        [self.district(geometry_case.geometry)],
                    )
                message = str(raised.exception)
                self.assertIn("DEA01", message)
                self.assertIn("invalid geometry", message)

    def test_valid_topologies_are_accepted(self) -> None:
        for geometry_case in valid_cases():
            with self.subTest(case=geometry_case.name):
                accepted, rejected = quality.classify_chargers(
                    [charger("inside", 0.5, 0.5)],
                    [self.district(geometry_case.geometry)],
                )
                self.assertEqual(len(accepted) + len(rejected), 1)

    def test_a_degenerate_district_no_longer_claims_a_station(self) -> None:
        """The former containment check accepted a zero-area district and assigned a station to it."""
        with self.assertRaisesRegex(ValueError, "DEA01 has invalid geometry"):
            quality.classify_chargers(
                [charger("at-the-point", 6.0, 50.0)],
                [self.district(case("degenerate_point").geometry)],
            )

    def test_a_self_intersecting_outline_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "Self-intersection"):
            quality.classify_chargers(
                [charger("inside", 6.5, 50.5)],
                [self.district(case("bowtie").geometry)],
            )

    def test_an_invalid_district_fails_before_any_station_is_assigned(self) -> None:
        """Validation runs over every district up front, not lazily per point."""
        districts = [
            square_region("DEA01", "West", 0.0, 1.0),
            self.district(case("hole_outside_shell").geometry, code="DEA02"),
        ]
        with self.assertRaisesRegex(ValueError, "DEA02 has invalid geometry"):
            quality.classify_chargers([charger("in-first-district", 0.5, 0.5)], districts)

    def test_a_valid_hole_still_excludes_only_its_interior(self) -> None:
        """Control: rejecting invalid topology must not change valid boundaries."""
        district = self.district(case("valid_with_hole").geometry)
        accepted, rejected = quality.classify_chargers(
            [
                charger("in-hole", 0.5, 0.5),
                charger("on-hole-edge", 0.4, 0.5),
                charger("in-shell", 0.1, 0.1),
            ],
            [district],
        )

        self.assertEqual(
            [item["properties"]["id"] for item in accepted], ["on-hole-edge", "in-shell"]
        )
        self.assertEqual([item["reason"] for item in rejected], ["outside_nrw"])

    def test_the_catalogue_covers_every_invalid_family(self) -> None:
        names = {geometry_case.name for geometry_case in GEOMETRY_CASES}
        self.assertLessEqual(
            {
                "degenerate_point",
                "bowtie",
                "hole_outside_shell",
                "overlapping_multipolygon",
                "valid_with_hole",
            },
            names,
        )


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
