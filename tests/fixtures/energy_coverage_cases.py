"""Shared catalogue of district energy-coverage cases (F14 / F13 / C03).

This is a deliberately synthetic fixture catalogue used to regression-test
how municipal-level energy rows are rolled up into district figures. The
rules under test are, in short: a district total may only be published when
every municipality expected to report for that district actually reported
in the relevant year(s); a municipality reporting a measured value of zero
still counts as reported; and a district's incompleteness must never leak
into another district's totals.

Two raw municipal inputs feed a district roll-up, keyed by ``(year, ags)``:
a consumption input (``consumption_gwh``) and a renewable input
(``published_generation_mwh``, ``wind_capacity_mw``,
``renewable_net_addition_mw``). For each case below, every field of the
``ExpectedDistrict`` records was worked out by hand from the raw rows -- the
accompanying test module recomputes each of them independently from the raw
rows and checks they match, so a transcription mistake here fails loudly
rather than silently validating nothing.

This module is test-support code, not production code. It intentionally has
no dependency on anything beyond the standard library so that it stays
trivially importable from a non-Python (SQL) test suite as well: it is pure
data plus a couple of tiny lookup helpers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsumptionRow:
    """One municipal consumption row."""

    year: int
    nuts_code: str
    ags: str
    consumption_gwh: float | None


@dataclass(frozen=True)
class RenewableRow:
    """One municipal renewable-stock/growth row."""

    year: int
    nuts_code: str
    ags: str
    published_generation_mwh: float | None
    wind_capacity_mw: float | None
    renewable_net_addition_mw: float | None


@dataclass(frozen=True)
class ExpectedDistrict:
    """The hand-derived, expected district roll-up for one case."""

    nuts_code: str
    reporting_year: int
    expected_municipalities: int
    consumption_municipalities_reported: int
    consumption_mwh: float | None
    renewable_municipalities_reported: int
    growth_years_reported: int
    renewable_net_addition_3y_mw: float | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class CoverageCase:
    """One named scenario: raw municipal rows plus the expected roll-up(s)."""

    name: str
    description: str
    consumption: tuple[ConsumptionRow, ...]
    renewable: tuple[RenewableRow, ...]
    expected: tuple[ExpectedDistrict, ...]


COVERAGE_CASES: tuple[CoverageCase, ...] = (
    # -- fully reported: the baseline control ------------------------------
    CoverageCase(
        name="complete_two_municipalities",
        description=(
            "One district, two municipalities, all three years and all "
            "fields present. Every total is available and nothing is "
            "unavailable."
        ),
        consumption=(
            ConsumptionRow(2022, "DEA01", "05111000", 100.0),
            ConsumptionRow(2022, "DEA01", "05111001", 50.0),
            ConsumptionRow(2023, "DEA01", "05111000", 110.0),
            ConsumptionRow(2023, "DEA01", "05111001", 55.0),
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            ConsumptionRow(2024, "DEA01", "05111001", 60.0),
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 100.0, 5.0, 0.5),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 105.0, 5.2, 0.5),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 110.0, 5.4, 0.5),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=2,
                consumption_mwh=180000.0,  # (120.0 + 60.0) * 1000
                renewable_municipalities_reported=2,
                growth_years_reported=3,
                renewable_net_addition_3y_mw=4.5,  # (1.0*3) + (0.5*3)
                unavailable_reason=None,
            ),
        ),
    ),
    # -- consumption coverage fails ------------------------------------------
    CoverageCase(
        name="municipality_missing_in_reporting_year",
        description=(
            "Two municipalities are in the district's universe, but only "
            "one reports consumption in the reporting year, so the "
            "consumption total must not be published even though renewable "
            "coverage and the growth window are both complete."
        ),
        consumption=(
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            # 05111001 has no consumption row at all in 2024.
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 90.0, 4.0, 0.5),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 95.0, 4.2, 0.5),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 110.0, 5.4, 0.5),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=1,
                consumption_mwh=None,
                renewable_municipalities_reported=2,
                growth_years_reported=3,
                renewable_net_addition_3y_mw=4.5,  # (1.0*3) + (0.5*3)
                unavailable_reason="incomplete_consumption_coverage",
            ),
        ),
    ),
    # -- a measured zero is not missing --------------------------------------
    CoverageCase(
        name="municipality_reports_a_true_zero",
        description=(
            "One municipality reports consumption_gwh = 0.0 every year. "
            "That is a reported measurement, not a missing value: coverage "
            "stays complete and the zero contributes 0.0 to the district "
            "total."
        ),
        consumption=(
            ConsumptionRow(2022, "DEA01", "05111000", 100.0),
            ConsumptionRow(2022, "DEA01", "05111001", 0.0),
            ConsumptionRow(2023, "DEA01", "05111000", 110.0),
            ConsumptionRow(2023, "DEA01", "05111001", 0.0),
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            ConsumptionRow(2024, "DEA01", "05111001", 0.0),
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 90.0, 4.0, 0.4),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 95.0, 4.2, 0.4),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 100.0, 4.4, 0.4),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=2,
                consumption_mwh=120000.0,  # (120.0 + 0.0) * 1000
                renewable_municipalities_reported=2,
                growth_years_reported=3,
                renewable_net_addition_3y_mw=4.2,  # (1.0*3) + (0.4*3)
                unavailable_reason=None,
            ),
        ),
    ),
    # -- growth window fails --------------------------------------------------
    CoverageCase(
        name="growth_year_missing",
        description=(
            "Consumption and renewable stock are both fully reported in "
            "the reporting year, but one municipality has no "
            "renewable_net_addition_mw for one year of the three-year "
            "growth window, so the 3-year addition total is unavailable."
        ),
        consumption=(
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            ConsumptionRow(2024, "DEA01", "05111001", 60.0),
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 90.0, 4.0, 0.5),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 95.0, 4.2, None),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 110.0, 5.4, 0.5),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=2,
                consumption_mwh=180000.0,  # (120.0 + 60.0) * 1000
                renewable_municipalities_reported=2,
                growth_years_reported=2,  # 2023 fails: 05111001 net-add is null
                renewable_net_addition_3y_mw=None,
                unavailable_reason="incomplete_growth_window",
            ),
        ),
    ),
    # -- renewable stock coverage fails ---------------------------------------
    CoverageCase(
        name="renewable_stock_incomplete",
        description=(
            "Consumption is fully reported, but one municipality has "
            "published_generation_mwh without wind_capacity_mw in the "
            "reporting year, so the renewable stock is incomplete even "
            "though the growth window is complete."
        ),
        consumption=(
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            ConsumptionRow(2024, "DEA01", "05111001", 60.0),
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 90.0, 4.0, 0.5),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 95.0, 4.2, 0.5),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 110.0, None, 0.5),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=2,
                consumption_mwh=180000.0,  # (120.0 + 60.0) * 1000
                renewable_municipalities_reported=1,  # 05111001 wind is null
                growth_years_reported=3,
                renewable_net_addition_3y_mw=4.5,  # (1.0*3) + (0.5*3)
                unavailable_reason="incomplete_renewable_coverage",
            ),
        ),
    ),
    # -- independence across districts ---------------------------------------
    CoverageCase(
        name="two_districts_independent",
        description=(
            "Two districts in the same batch: DEA01 is fully complete and "
            "DEA02 is missing a municipality's reporting-year consumption. "
            "DEA02's incompleteness must not affect DEA01's totals."
        ),
        consumption=(
            ConsumptionRow(2022, "DEA01", "05111000", 100.0),
            ConsumptionRow(2022, "DEA01", "05111001", 50.0),
            ConsumptionRow(2023, "DEA01", "05111000", 110.0),
            ConsumptionRow(2023, "DEA01", "05111001", 55.0),
            ConsumptionRow(2024, "DEA01", "05111000", 120.0),
            ConsumptionRow(2024, "DEA01", "05111001", 60.0),
            ConsumptionRow(2024, "DEA02", "05112000", 200.0),
            # DEA02 / 05112001 has no consumption row at all in 2024.
        ),
        renewable=(
            RenewableRow(2022, "DEA01", "05111000", 200.0, 10.0, 1.0),
            RenewableRow(2022, "DEA01", "05111001", 100.0, 5.0, 0.5),
            RenewableRow(2023, "DEA01", "05111000", 210.0, 10.5, 1.0),
            RenewableRow(2023, "DEA01", "05111001", 105.0, 5.2, 0.5),
            RenewableRow(2024, "DEA01", "05111000", 220.0, 11.0, 1.0),
            RenewableRow(2024, "DEA01", "05111001", 110.0, 5.4, 0.5),
            RenewableRow(2022, "DEA02", "05112000", 300.0, 15.0, 2.0),
            RenewableRow(2022, "DEA02", "05112001", 150.0, 7.0, 1.0),
            RenewableRow(2023, "DEA02", "05112000", 310.0, 15.5, 2.0),
            RenewableRow(2023, "DEA02", "05112001", 155.0, 7.2, 1.0),
            RenewableRow(2024, "DEA02", "05112000", 320.0, 16.0, 2.0),
            RenewableRow(2024, "DEA02", "05112001", 160.0, 7.4, 1.0),
        ),
        expected=(
            ExpectedDistrict(
                nuts_code="DEA01",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=2,
                consumption_mwh=180000.0,  # (120.0 + 60.0) * 1000
                renewable_municipalities_reported=2,
                growth_years_reported=3,
                renewable_net_addition_3y_mw=4.5,  # (1.0*3) + (0.5*3)
                unavailable_reason=None,
            ),
            ExpectedDistrict(
                nuts_code="DEA02",
                reporting_year=2024,
                expected_municipalities=2,
                consumption_municipalities_reported=1,
                consumption_mwh=None,
                renewable_municipalities_reported=2,
                growth_years_reported=3,
                renewable_net_addition_3y_mw=9.0,  # (2.0*3) + (1.0*3)
                unavailable_reason="incomplete_consumption_coverage",
            ),
        ),
    ),
)


def case(name: str) -> CoverageCase:
    """Look up a :class:`CoverageCase` by name, or raise ``KeyError(name)``."""
    for entry in COVERAGE_CASES:
        if entry.name == name:
            return entry
    raise KeyError(name)


def expected_for(case_name: str, nuts_code: str) -> ExpectedDistrict:
    """Look up one district's :class:`ExpectedDistrict` within a case.

    Raises ``KeyError(case_name)`` if the case does not exist, or
    ``KeyError((case_name, nuts_code))`` if the case exists but has no
    expected district for ``nuts_code``.
    """
    entry = case(case_name)
    for district in entry.expected:
        if district.nuts_code == nuts_code:
            return district
    raise KeyError((case_name, nuts_code))


def _sql_number(value: float | None) -> str:
    """Render a float (or ``None``) as a SQL numeric literal."""
    return "NULL" if value is None else repr(value)


def consumption_values_sql(case: CoverageCase) -> str:
    """Render ``case.consumption`` as a SQL ``VALUES`` list body.

    Column order: ``(year, nuts_code, ags, consumption_gwh)``. One row per
    line, ``None`` rendered as ``NULL``, no trailing comma after the last
    row.
    """
    lines = [
        "({year}, '{nuts_code}', '{ags}', {consumption_gwh})".format(
            year=row.year,
            nuts_code=row.nuts_code,
            ags=row.ags,
            consumption_gwh=_sql_number(row.consumption_gwh),
        )
        for row in case.consumption
    ]
    return ",\n".join(lines)


def renewable_values_sql(case: CoverageCase) -> str:
    """Render ``case.renewable`` as a SQL ``VALUES`` list body.

    Column order: ``(year, nuts_code, ags, published_generation_mwh,
    wind_capacity_mw, renewable_net_addition_mw)``. One row per line,
    ``None`` rendered as ``NULL``, no trailing comma after the last row.
    """
    lines = [
        "({year}, '{nuts_code}', '{ags}', {published}, {wind}, {net_addition})".format(
            year=row.year,
            nuts_code=row.nuts_code,
            ags=row.ags,
            published=_sql_number(row.published_generation_mwh),
            wind=_sql_number(row.wind_capacity_mw),
            net_addition=_sql_number(row.renewable_net_addition_mw),
        )
        for row in case.renewable
    ]
    return ",\n".join(lines)
