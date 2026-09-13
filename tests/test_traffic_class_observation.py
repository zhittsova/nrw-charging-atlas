"""Traffic class-zero regression cases.

The publisher defines no no-data code, so treating a reading as unpublished is
the project's own inference. It applies to a whole section, not to one vehicle
class: a road with no heavy goods traffic reports a real zero heavy count, and
erasing it would destroy an observation the source actually published.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))

import load_nrw_infrastructure_postgis as loader  # noqa: E402
from traffic_class_cases import TRAFFIC_ROWS, published_rows, row, unpublished_rows  # noqa: E402


def observe(case) -> tuple[float | None, float | None, float | None]:
    return loader.traffic_observation(
        {
            "DTVKFZA": case.total_all_days,
            "DTVKFZW": case.total_working,
            "DTVKFZU": case.total_holiday,
            "DTVKFZS": case.total_sunday,
        },
        case.light,
        case.heavy,
    )


class TrafficObservationTest(unittest.TestCase):
    def test_every_catalogued_row_stores_the_expected_values(self) -> None:
        for case in TRAFFIC_ROWS:
            with self.subTest(case=case.name):
                self.assertEqual(
                    observe(case),
                    (case.expected_total, case.expected_light, case.expected_heavy),
                    case.description,
                )

    def test_a_zero_heavy_count_on_a_busy_road_survives(self) -> None:
        """The Traffic class-zero counterexample: this used to be erased to unknown."""
        total, light, heavy = observe(row("zero_heavy_on_a_busy_road"))

        self.assertEqual(total, 100.0)
        self.assertEqual(light, 100.0)
        self.assertEqual(heavy, 0.0)
        self.assertIsNotNone(heavy, "a measured zero heavy count became unknown")

    def test_a_zero_light_count_survives(self) -> None:
        self.assertEqual(observe(row("zero_light_with_heavy_traffic")), (600.0, 0.0, 600.0))

    def test_a_station_that_published_nothing_is_wholly_unknown(self) -> None:
        self.assertEqual(observe(row("all_day_types_zero_manual_station")), (None, None, None))

    def test_a_positive_total_with_no_classes_is_an_absent_split(self) -> None:
        """A total cannot consist of neither light nor heavy vehicles."""
        total, light, heavy = observe(row("class_split_absent"))

        self.assertEqual(total, 11454.0)
        self.assertIsNone(light)
        self.assertIsNone(heavy)

    def test_the_catalogue_separates_published_from_unpublished_rows(self) -> None:
        self.assertEqual(
            len(published_rows()) + len(unpublished_rows()), len(TRAFFIC_ROWS)
        )
        self.assertIn(
            "all_day_types_zero_manual_station",
            {case.name for case in unpublished_rows()},
        )

    def test_a_zero_all_days_total_contradicted_by_a_day_type_is_refused(self) -> None:
        """The inference is checked on every load, not assumed to still hold."""
        with self.assertRaisesRegex(ValueError, "no longer holds"):
            loader.traffic_observation(
                {"DTVKFZA": 0, "DTVKFZW": 5000, "DTVKFZU": 0, "DTVKFZS": 0}, 0, 0
            )

    def test_non_finite_and_negative_readings_are_still_refused(self) -> None:
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    loader._traffic_value(value, field="DTVKFZA")
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            loader._traffic_value(-1.0, field="DTVSVA")

    def test_a_measured_zero_passes_field_validation_unchanged(self) -> None:
        self.assertEqual(loader._traffic_value(0.0, field="DTVSVA"), 0.0)


if __name__ == "__main__":
    unittest.main()
