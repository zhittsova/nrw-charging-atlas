from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tests" / "fixtures"))

from projected_geometry import (  # noqa: E402
    DEGREE_ORDER_COUNTEREXAMPLE,
    nearest_by_degrees,
    nearest_by_metres,
    projected_distance_m,
    projected_point_to_segment_distance_m,
)


class DegreeVersusMetreOrderingTest(unittest.TestCase):
    def test_degree_ordering_and_metre_ordering_disagree(self) -> None:
        origin = DEGREE_ORDER_COUNTEREXAMPLE["origin"]
        candidates = DEGREE_ORDER_COUNTEREXAMPLE["candidates"]

        degree_winner_id, _ = nearest_by_degrees(origin, candidates)
        metre_winner_id, _ = nearest_by_metres(origin, candidates)

        self.assertEqual(degree_winner_id, DEGREE_ORDER_COUNTEREXAMPLE["degree_nearest"])
        self.assertEqual(metre_winner_id, DEGREE_ORDER_COUNTEREXAMPLE["metre_nearest"])
        self.assertNotEqual(degree_winner_id, metre_winner_id)

    def test_metre_distances_are_pinned_within_tolerance(self) -> None:
        origin = DEGREE_ORDER_COUNTEREXAMPLE["origin"]
        candidates = DEGREE_ORDER_COUNTEREXAMPLE["candidates"]

        north_distance_m = projected_distance_m(origin, candidates["north"])
        east_distance_m = projected_distance_m(origin, candidates["east"])

        # "north" (degree-nearest) is actually the farther candidate in metres.
        self.assertAlmostEqual(north_distance_m, 2780.7, delta=50.0)
        # "east" (metre-nearest) is actually the closer candidate in metres.
        self.assertAlmostEqual(east_distance_m, 2082.6, delta=50.0)
        # A degree-ordered nearest query therefore misreports the distance by
        # several hundred metres, the same failure mode as the DEA45 audit case.
        self.assertGreater(north_distance_m - east_distance_m, 500.0)


class ProjectedDistanceOracleTest(unittest.TestCase):
    """Checks the oracle itself against a hand-derived, pyproj-free expectation."""

    def test_east_west_offset_matches_hand_derived_metres_per_degree(self) -> None:
        lat = 51.5
        offset_degrees = 0.02
        a = (7.6, lat)
        b = (7.6 + offset_degrees, lat)

        expected_m = offset_degrees * 111_320 * math.cos(math.radians(lat))
        actual_m = projected_distance_m(a, b)

        self.assertAlmostEqual(actual_m, expected_m, delta=expected_m * 0.01)

    def test_north_south_offset_matches_hand_derived_metres_per_degree(self) -> None:
        lat = 51.5
        offset_degrees = 0.02
        a = (7.6, lat)
        b = (7.6, lat + offset_degrees)

        expected_m = offset_degrees * 111_132
        actual_m = projected_distance_m(a, b)

        self.assertAlmostEqual(actual_m, expected_m, delta=expected_m * 0.01)


class ProjectedPointToSegmentDistanceTest(unittest.TestCase):
    def test_point_beside_segment_midpoint_returns_perpendicular_distance(self) -> None:
        seg_start = (7.6, 51.5)
        seg_end = (7.6, 51.52)
        # Roughly abeam the segment's midpoint, offset east by ~0.01 deg.
        point = (7.61, 51.51)

        distance = projected_point_to_segment_distance_m(point, seg_start, seg_end)
        direct_to_midpoint = projected_distance_m(point, (7.6, 51.51))

        # The perpendicular foot lands near the midpoint, so the segment
        # distance should be close to (and no larger than) the distance to
        # the midpoint itself.
        self.assertLessEqual(distance, direct_to_midpoint + 1.0)
        self.assertGreater(distance, 0.0)
        self.assertAlmostEqual(distance, direct_to_midpoint, delta=5.0)

    def test_point_beyond_endpoint_clamps_to_endpoint_distance(self) -> None:
        seg_start = (7.6, 51.5)
        seg_end = (7.6, 51.51)
        point = (7.6, 51.53)  # north of both endpoints, beyond seg_end

        distance = projected_point_to_segment_distance_m(point, seg_start, seg_end)
        expected = projected_distance_m(point, seg_end)

        self.assertAlmostEqual(distance, expected, delta=0.01)


class NearestByMetresTieBreakTest(unittest.TestCase):
    def test_equidistant_candidates_break_ties_deterministically_by_id(self) -> None:
        origin = (7.6, 51.5)
        # Two different features sitting at the exact same coordinate are
        # necessarily equidistant from the origin (to the bit), so any
        # ordering between them can only come from the id tie-break.
        same_point = (7.61, 51.5)
        candidates = {
            "zeta": same_point,
            "alpha": same_point,
        }

        winner_id, _ = nearest_by_metres(origin, candidates)

        self.assertEqual(winner_id, "alpha")

    def test_tie_break_is_stable_regardless_of_dict_insertion_order(self) -> None:
        origin = (7.6, 51.5)
        same_point = (7.61, 51.5)
        candidates_a = {"alpha": same_point, "zeta": same_point}
        candidates_b = {"zeta": same_point, "alpha": same_point}

        self.assertEqual(
            nearest_by_metres(origin, candidates_a),
            nearest_by_metres(origin, candidates_b),
        )


if __name__ == "__main__":
    unittest.main()
