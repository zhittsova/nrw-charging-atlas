"""Prove the shared energy-coverage catalogue's hand-derived numbers are real.

``tests/fixtures/energy_coverage_cases.py`` declares, for each named
scenario, the district roll-up (municipal universe size, reporting year,
reported counts, summed totals, and unavailability reason) that F14/F13/C03
require. This module does not trust those declarations -- for every case it
recomputes each number directly from the raw municipal rows with small,
explicit loops (never by importing or re-using any production aggregation
code, so there is no risk of the check circularly agreeing with a buggy
implementation) and asserts the fixture's literals match. A future mistake
in the fixture then fails loudly here rather than silently poisoning the
Python and PostGIS suites that also read it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tests" / "fixtures"))

from energy_coverage_cases import (  # noqa: E402
    COVERAGE_CASES,
    CoverageCase,
    ConsumptionRow,
    RenewableRow,
    case,
    consumption_values_sql,
    expected_for,
    renewable_values_sql,
)


def _municipal_universe(coverage_case: CoverageCase, nuts_code: str) -> set[str]:
    """Every distinct ags for this district across all years, either input."""
    ags_set: set[str] = set()
    for row in coverage_case.consumption:
        if row.nuts_code == nuts_code:
            ags_set.add(row.ags)
    for row in coverage_case.renewable:
        if row.nuts_code == nuts_code:
            ags_set.add(row.ags)
    return ags_set


class ExpectedMunicipalitiesTest(unittest.TestCase):
    def test_universe_is_distinct_ags_across_both_inputs_and_all_years(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    universe = _municipal_universe(coverage_case, expected.nuts_code)
                    self.assertEqual(len(universe), expected.expected_municipalities)


class ReportingYearTest(unittest.TestCase):
    def test_reporting_year_is_highest_year_with_both_inputs_present(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    consumption_years = {
                        row.year
                        for row in coverage_case.consumption
                        if row.nuts_code == expected.nuts_code
                    }
                    renewable_complete_years = {
                        row.year
                        for row in coverage_case.renewable
                        if row.nuts_code == expected.nuts_code
                        and row.published_generation_mwh is not None
                        and row.wind_capacity_mw is not None
                    }
                    candidate_years = consumption_years & renewable_complete_years
                    self.assertTrue(candidate_years)
                    self.assertEqual(max(candidate_years), expected.reporting_year)


class ConsumptionCoverageTest(unittest.TestCase):
    def test_reported_count_and_conditional_sum(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    reported = [
                        row
                        for row in coverage_case.consumption
                        if row.nuts_code == expected.nuts_code
                        and row.year == expected.reporting_year
                        and row.consumption_gwh is not None
                    ]
                    self.assertEqual(
                        len(reported), expected.consumption_municipalities_reported
                    )

                    universe = _municipal_universe(coverage_case, expected.nuts_code)
                    if len(reported) == len(universe):
                        total_mwh = sum(row.consumption_gwh for row in reported) * 1000
                        self.assertEqual(expected.consumption_mwh, total_mwh)
                    else:
                        self.assertIsNone(expected.consumption_mwh)

    def test_a_measured_zero_counts_as_reported(self) -> None:
        # Direct check of the F13/C03 control case: a row with
        # consumption_gwh == 0.0 must be counted as reported, not missing.
        zero_case = case("municipality_reports_a_true_zero")
        reporting_year_rows = [
            row for row in zero_case.consumption if row.year == 2024
        ]
        zero_rows = [row for row in reporting_year_rows if row.consumption_gwh == 0.0]
        self.assertEqual(len(zero_rows), 1)
        reported = [row for row in reporting_year_rows if row.consumption_gwh is not None]
        self.assertEqual(len(reported), 2)
        expected = expected_for("municipality_reports_a_true_zero", "DEA01")
        self.assertEqual(expected.consumption_municipalities_reported, 2)
        self.assertEqual(expected.consumption_mwh, 120000.0)
        self.assertIsNone(expected.unavailable_reason)


class RenewableStockCoverageTest(unittest.TestCase):
    def test_reported_count_requires_both_fields_non_null(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    reported = [
                        row
                        for row in coverage_case.renewable
                        if row.nuts_code == expected.nuts_code
                        and row.year == expected.reporting_year
                        and row.published_generation_mwh is not None
                        and row.wind_capacity_mw is not None
                    ]
                    self.assertEqual(
                        len(reported), expected.renewable_municipalities_reported
                    )


class GrowthWindowTest(unittest.TestCase):
    def test_years_reported_and_conditional_sum(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    universe = _municipal_universe(coverage_case, expected.nuts_code)
                    window_years = range(
                        expected.reporting_year - 2, expected.reporting_year + 1
                    )

                    years_reported = 0
                    window_rows = [
                        row
                        for row in coverage_case.renewable
                        if row.nuts_code == expected.nuts_code
                        and row.year in window_years
                    ]
                    for year in window_years:
                        net_addition_by_ags = {
                            row.ags: row.renewable_net_addition_mw
                            for row in window_rows
                            if row.year == year
                        }
                        year_complete = all(
                            net_addition_by_ags.get(ags) is not None for ags in universe
                        )
                        if year_complete:
                            years_reported += 1

                    self.assertEqual(years_reported, expected.growth_years_reported)

                    if years_reported == 3:
                        total = sum(
                            row.renewable_net_addition_mw
                            for row in window_rows
                            if row.renewable_net_addition_mw is not None
                        )
                        self.assertEqual(expected.renewable_net_addition_3y_mw, total)
                    else:
                        self.assertIsNone(expected.renewable_net_addition_3y_mw)


class UnavailableReasonPrecedenceTest(unittest.TestCase):
    def test_first_incomplete_dimension_wins(self) -> None:
        for coverage_case in COVERAGE_CASES:
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    if (
                        expected.consumption_municipalities_reported
                        != expected.expected_municipalities
                    ):
                        self.assertEqual(
                            expected.unavailable_reason, "incomplete_consumption_coverage"
                        )
                    elif (
                        expected.renewable_municipalities_reported
                        != expected.expected_municipalities
                    ):
                        self.assertEqual(
                            expected.unavailable_reason, "incomplete_renewable_coverage"
                        )
                    elif expected.growth_years_reported != 3:
                        self.assertEqual(
                            expected.unavailable_reason, "incomplete_growth_window"
                        )
                    else:
                        self.assertIsNone(expected.unavailable_reason)


class SqlRenderingTest(unittest.TestCase):
    def test_consumption_values_sql_renders_null_quotes_and_no_trailing_comma(
        self,
    ) -> None:
        synthetic = CoverageCase(
            name="synthetic",
            description="synthetic case for SQL rendering checks",
            consumption=(
                ConsumptionRow(2024, "DEA01", "05111000", 100.0),
                ConsumptionRow(2024, "DEA01", "05111001", None),
            ),
            renewable=(),
            expected=(),
        )
        sql = consumption_values_sql(synthetic)
        lines = sql.split("\n")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "(2024, 'DEA01', '05111000', 100.0),")
        self.assertEqual(lines[1], "(2024, 'DEA01', '05111001', NULL)")
        self.assertFalse(lines[-1].endswith(","))

    def test_renewable_values_sql_renders_null_quotes_and_no_trailing_comma(
        self,
    ) -> None:
        synthetic = CoverageCase(
            name="synthetic",
            description="synthetic case for SQL rendering checks",
            consumption=(),
            renewable=(
                RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
                RenewableRow(2024, "DEA01", "05111001", 110.0, None, 0.5),
            ),
            expected=(),
        )
        sql = renewable_values_sql(synthetic)
        lines = sql.split("\n")

        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0], "(2024, 'DEA01', '05111000', 220.0, 11.0, 1.0),"
        )
        self.assertEqual(
            lines[1], "(2024, 'DEA01', '05111001', 110.0, NULL, 0.5)"
        )
        self.assertFalse(lines[-1].endswith(","))

    def test_sql_helpers_cover_every_declared_case_without_error(self) -> None:
        for coverage_case in COVERAGE_CASES:
            with self.subTest(case=coverage_case.name):
                consumption_sql = consumption_values_sql(coverage_case)
                renewable_sql = renewable_values_sql(coverage_case)
                self.assertEqual(
                    len(consumption_sql.split("\n")), len(coverage_case.consumption)
                )
                self.assertEqual(
                    len(renewable_sql.split("\n")), len(coverage_case.renewable)
                )


class LookupHelpersTest(unittest.TestCase):
    def test_case_raises_key_error_for_unknown_name(self) -> None:
        with self.assertRaises(KeyError):
            case("nope")

    def test_expected_for_returns_matching_district(self) -> None:
        expected = expected_for("two_districts_independent", "DEA02")
        self.assertEqual(expected.nuts_code, "DEA02")
        self.assertEqual(expected.unavailable_reason, "incomplete_consumption_coverage")

    def test_expected_for_raises_key_error_for_unknown_district(self) -> None:
        with self.assertRaises(KeyError):
            expected_for("two_districts_independent", "DEA99")


if __name__ == "__main__":
    unittest.main()
