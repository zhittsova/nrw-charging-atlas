"""Independent S08 expectation catalogue for the canonical SQL score model.

This module is the independent expectation catalogue for the district
scoring model implemented in the project's canonical analytics SQL
(``db/nrw_analytics.sql``): the 5th/95th-percentile normalization of each
raw indicator, the weighted composites built on top of the normalized
indicators, and the ``RANK() OVER (... DESC NULLS LAST)`` used to rank
districts by investment priority.

Every helper and every literal expected value here was derived from the
written S08 contract -- percentile_cont semantics, the normalize-then-clip
formula, the "publish rounded, then compose from the rounded values"
rounding rule, PostgreSQL's half-away-from-zero ``numeric`` rounding, and
the null-propagation rule that weights are never redistributed -- and
*not* from reading the SQL itself. The arithmetic is deliberately
duplicated here, by hand, so that a defect in the SQL implementation
cannot hide behind a test that was really just checking the SQL against
itself.

This module is test-support code, not production code. It intentionally
has no dependency on anything beyond the standard library so that it stays
trivially importable from a non-Python (SQL) test suite as well: it is
pure data plus a handful of small, self-contained helpers that a test
elsewhere is expected to check *against* this catalogue, not the other way
around.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def round_half_up(value: float | None, digits: int = 1) -> float | None:
    """Round ``value`` to ``digits`` decimal places, half away from zero.

    This reproduces PostgreSQL ``numeric`` rounding (0.05 -> 0.1, 2.25 ->
    2.3, -0.05 -> -0.1), which is *not* what Python's built-in ``round``
    does (that uses banker's rounding). ``None`` passes through unchanged.
    """
    if value is None:
        return None
    quantizer = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP))


def percentile_cont(values: list[float | None] | tuple[float | None, ...], fraction: float) -> float | None:
    """Reproduce PostgreSQL's ``percentile_cont(fraction) WITHIN GROUP (ORDER BY x)``.

    NULLs are excluded before ordering. With ``n`` remaining values sorted
    ascending, ``pos = fraction * (n - 1)`` selects a (possibly
    fractional) rank; the two bracketing values are linearly interpolated.
    An empty (post-NULL-removal) input yields ``None``.
    """
    remaining = sorted(v for v in values if v is not None)
    n = len(remaining)
    if n == 0:
        return None
    pos = fraction * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return remaining[lo]
    return remaining[lo] + (pos - lo) * (remaining[hi] - remaining[lo])


def bounds_5_95(values: list[float | None] | tuple[float | None, ...]) -> tuple[float | None, float | None]:
    """The project's normalization bounds: the 5th and 95th percentiles."""
    return (percentile_cont(values, 0.05), percentile_cont(values, 0.95))


def normalize_5_95(
    metric: float | None,
    lower_bound: float | None,
    upper_bound: float | None,
    inverse: bool = False,
) -> float | None:
    """Normalize ``metric`` into ``[0, 100]`` against ``[lower_bound, upper_bound]``.

    ``metric`` is clipped into the bound interval first. Equal (degenerate)
    bounds always yield exactly 50.0, in both directions. Any ``None``
    input (metric or either bound) yields ``None``. The result is an
    unrounded float; callers are responsible for publishing it with
    :func:`round_half_up`.
    """
    if metric is None or lower_bound is None or upper_bound is None:
        return None
    if upper_bound == lower_bound:
        return 50.0
    clipped = min(max(metric, lower_bound), upper_bound)
    pct = 100 * (clipped - lower_bound) / (upper_bound - lower_bound)
    return 100 - pct if inverse else pct


def weighted_composite(components: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted sum of already-published (rounded) component scores, rounded to 1 decimal.

    ``weights`` maps a component name to its weight. A weight key prefixed
    with ``"inverse:"`` means the term contributed is ``100 -
    components[<name>]`` rather than ``components[<name>]`` directly --
    this is how ``charger_deficit_score`` (100 - ev_readiness_score) and
    the ``(100 - grid_readiness_proxy_score)`` term of
    ``grid_absorption_risk_proxy_score`` are expressed without a separate
    code path.

    If any required component is missing or ``None``, the whole composite
    is ``None``. Weights are never redistributed over the components that
    happen to be present -- a composite with one missing input has no
    value, not a re-weighted partial value.
    """
    total = Decimal("0")
    for key, weight in weights.items():
        if key.startswith("inverse:"):
            name = key[len("inverse:") :]
            raw = components.get(name)
            if raw is None:
                return None
            term = Decimal("100") - Decimal(str(raw))
        else:
            value = components.get(key)
            if value is None:
                return None
            term = Decimal(str(value))
        total += Decimal(str(weight)) * term
    return round_half_up(float(total), 1)


def rank_desc_nulls_last(scores: list[float | None]) -> list[int]:
    """Reproduce ``RANK() OVER (ORDER BY score DESC NULLS LAST)``.

    Ties share a rank; the next distinct (lower) score skips ranks by the
    size of the tie group it follows. Every ``None`` score sorts after
    every real score and all ``None`` entries share one rank -- the rank
    that immediately follows the ranked (non-``None``) scores.
    """
    n = len(scores)
    ranks: list[int] = [0] * n
    non_null = [(i, s) for i, s in enumerate(scores) if s is not None]
    non_null.sort(key=lambda pair: pair[1], reverse=True)
    for pos, (i, s) in enumerate(non_null):
        if pos == 0 or s != non_null[pos - 1][1]:
            ranks[i] = pos + 1
        else:
            ranks[i] = ranks[non_null[pos - 1][0]]
    null_rank = len(non_null) + 1
    for i, s in enumerate(scores):
        if s is None:
            ranks[i] = null_rank
    return ranks


# ---------------------------------------------------------------------------
# Indicator weights
# ---------------------------------------------------------------------------

INDICATOR_WEIGHTS: dict[str, dict[str, float]] = {
    "ev_readiness_score": {
        "charger_density_score": 0.40,
        "charger_accessibility_score": 0.30,
        "population_adjusted_coverage_score": 0.30,
    },
    "charger_deficit_score": {
        "inverse:ev_readiness_score": 1.00,
    },
    "transport_load_score": {
        "traffic_intensity_score": 0.60,
        "traffic_road_density_score": 0.25,
        "road_proximity_score": 0.15,
    },
    "grid_readiness_proxy_score": {
        "substation_proximity_score": 0.45,
        "voltage_line_density_score": 0.35,
        "substation_density_score": 0.20,
    },
    "renewable_context_score": {
        "renewable_capacity_density_score": 0.70,
        "renewable_technology_diversity_score": 0.30,
    },
    "infrastructure_opportunity_score": {
        "transport_load_score": 0.40,
        "grid_readiness_proxy_score": 0.35,
        "renewable_context_score": 0.25,
    },
    "investment_priority_score": {
        "charger_deficit_score": 0.60,
        "infrastructure_opportunity_score": 0.40,
    },
    "grid_absorption_risk_proxy_score": {
        "local_energy_balance_score": 0.45,
        "renewable_growth_score": 0.30,
        "inverse:grid_readiness_proxy_score": 0.25,
    },
}


# ---------------------------------------------------------------------------
# Normalization cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizationCase:
    """One hand-computed :func:`normalize_5_95` case."""

    label: str
    metric: float | None
    lower_bound: float | None
    upper_bound: float | None
    inverse: bool
    expected: float | None
    note: str


NORMALIZATION_CASES: tuple[NormalizationCase, ...] = (
    # -- non-degenerate bounds [10, 50], non-inverse ------------------------
    NormalizationCase(
        "at_lower_bound_normal", 10.0, 10.0, 50.0, False, 0.0,
        "Exactly at the lower bound: 100*(10-10)/40 = 0.0.",
    ),
    NormalizationCase(
        "at_upper_bound_normal", 50.0, 10.0, 50.0, False, 100.0,
        "Exactly at the upper bound: 100*(50-10)/40 = 100.0.",
    ),
    NormalizationCase(
        "midpoint_normal", 30.0, 10.0, 50.0, False, 50.0,
        "Midpoint of [10,50]: 100*(30-10)/40 = 50.0.",
    ),
    NormalizationCase(
        "below_lower_bound_clipped_normal", -5.0, 10.0, 50.0, False, 0.0,
        "Below the lower bound: clipped to 10 first, giving 0.0.",
    ),
    NormalizationCase(
        "above_upper_bound_clipped_normal", 120.0, 10.0, 50.0, False, 100.0,
        "Above the upper bound: clipped to 50 first, giving 100.0.",
    ),
    # -- same five, inverse direction ---------------------------------------
    NormalizationCase(
        "at_lower_bound_inverse", 10.0, 10.0, 50.0, True, 100.0,
        "Inverse flips the non-inverse 0.0 at the lower bound to 100.0.",
    ),
    NormalizationCase(
        "at_upper_bound_inverse", 50.0, 10.0, 50.0, True, 0.0,
        "Inverse flips the non-inverse 100.0 at the upper bound to 0.0.",
    ),
    NormalizationCase(
        "midpoint_inverse", 30.0, 10.0, 50.0, True, 50.0,
        "The midpoint is its own inverse: 100 - 50 = 50.0.",
    ),
    NormalizationCase(
        "below_lower_bound_clipped_inverse", -5.0, 10.0, 50.0, True, 100.0,
        "Clipped to the lower bound first, then inverted: 100 - 0 = 100.0.",
    ),
    NormalizationCase(
        "above_upper_bound_clipped_inverse", 120.0, 10.0, 50.0, True, 0.0,
        "Clipped to the upper bound first, then inverted: 100 - 100 = 0.0.",
    ),
    # -- equal finite bounds: always 50.0, both directions -------------------
    NormalizationCase(
        "equal_bounds_value_at_bound_normal", 30.0, 30.0, 30.0, False, 50.0,
        "Degenerate bounds short-circuit to 50.0 regardless of the metric.",
    ),
    NormalizationCase(
        "equal_bounds_value_below_normal", 10.0, 30.0, 30.0, False, 50.0,
        "Still 50.0 even though the metric is below the (single) bound value.",
    ),
    NormalizationCase(
        "equal_bounds_value_above_normal", 50.0, 30.0, 30.0, False, 50.0,
        "Still 50.0 even though the metric is above the (single) bound value.",
    ),
    NormalizationCase(
        "equal_bounds_value_at_bound_inverse", 30.0, 30.0, 30.0, True, 50.0,
        "The equal-bounds short-circuit ignores the inverse flag too.",
    ),
    NormalizationCase(
        "equal_bounds_value_below_inverse", 10.0, 30.0, 30.0, True, 50.0,
        "Inverse, metric below the bound: still 50.0.",
    ),
    NormalizationCase(
        "equal_bounds_value_above_inverse", 50.0, 30.0, 30.0, True, 50.0,
        "Inverse, metric above the bound: still 50.0.",
    ),
    # -- None propagation ------------------------------------------------------
    NormalizationCase(
        "none_metric_valid_bounds", None, 10.0, 50.0, False, None,
        "A missing metric yields None even with valid bounds.",
    ),
    NormalizationCase(
        "valid_metric_none_lower_bound", 30.0, None, 50.0, False, None,
        "A missing lower bound yields None even with a valid metric.",
    ),
    NormalizationCase(
        "valid_metric_none_upper_bound", 30.0, 10.0, None, False, None,
        "A missing upper bound yields None even with a valid metric.",
    ),
    # -- equal bounds of zero --------------------------------------------------
    NormalizationCase(
        "equal_bounds_zero_normal", 0.0, 0.0, 0.0, False, 50.0,
        "Degenerate zero bounds: still 50.0, not a division by zero.",
    ),
    NormalizationCase(
        "equal_bounds_zero_nonzero_metric_inverse", 99.0, 0.0, 0.0, True, 50.0,
        "Degenerate zero bounds with an unrelated metric value: still 50.0.",
    ),
    # -- negative bounds ---------------------------------------------------
    NormalizationCase(
        "negative_bounds_midpoint_normal", -30.0, -50.0, -10.0, False, 50.0,
        "Midpoint of [-50,-10]: 100*(-30-(-50))/40 = 100*20/40 = 50.0.",
    ),
    NormalizationCase(
        "negative_bounds_at_lower_normal", -50.0, -50.0, -10.0, False, 0.0,
        "At the (negative) lower bound: 100*0/40 = 0.0.",
    ),
    NormalizationCase(
        "negative_bounds_at_upper_inverse", -10.0, -50.0, -10.0, True, 0.0,
        "At the (negative) upper bound, inverse: 100 - 100 = 0.0.",
    ),
    NormalizationCase(
        "negative_bounds_below_clipped_normal", -80.0, -50.0, -10.0, False, 0.0,
        "Below the negative lower bound: clipped to -50, giving 0.0.",
    ),
)


# ---------------------------------------------------------------------------
# Percentile cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PercentileCase:
    """One hand-computed :func:`percentile_cont` case."""

    label: str
    values: tuple[float | None, ...]
    fraction: float
    expected: float | None
    note: str


PERCENTILE_CASES: tuple[PercentileCase, ...] = (
    PercentileCase(
        "single_value_lower_fraction", (42.0,), 0.05, 42.0,
        "n=1: pos = 0.05*0 = 0, so the sole value is the answer regardless of fraction.",
    ),
    PercentileCase(
        "single_value_upper_fraction", (42.0,), 0.95, 42.0,
        "n=1: pos = 0.95*0 = 0, again just the sole value.",
    ),
    PercentileCase(
        "two_values_p05", (10.0, 20.0), 0.05, 10.5,
        "n=2: pos = 0.05*1 = 0.05; interpolate 10 + 0.05*(20-10) = 10.5.",
    ),
    PercentileCase(
        "two_values_p95", (10.0, 20.0), 0.95, 19.5,
        "n=2: pos = 0.95*1 = 0.95; interpolate 10 + 0.95*(20-10) = 19.5.",
    ),
    PercentileCase(
        "three_values_p05", (10.0, 20.0, 30.0), 0.05, 11.0,
        "n=3: pos = 0.05*2 = 0.1; interpolate 10 + 0.1*(20-10) = 11.0.",
    ),
    PercentileCase(
        "three_values_p95", (10.0, 20.0, 30.0), 0.95, 29.0,
        "n=3: pos = 0.95*2 = 1.9; interpolate 20 + 0.9*(30-20) = 29.0.",
    ),
    PercentileCase(
        "list_with_nones_p50", (None, 10.0, None, 30.0, 20.0), 0.5, 20.0,
        "NULLs excluded first, leaving [10,20,30]; pos = 0.5*2 = 1.0 lands exactly on v[1] = 20.0.",
    ),
    PercentileCase(
        "all_none_list", (None, None, None), 0.5, None,
        "Nothing remains after excluding NULLs, so the result is None.",
    ),
    PercentileCase(
        "empty_list", (), 0.05, None,
        "An empty input has nothing to order, so the result is None.",
    ),
    PercentileCase(
        "exact_index_position", (1.0, 2.0, 3.0, 4.0, 5.0), 0.25, 2.0,
        "n=5: pos = 0.25*4 = 1.0 lands exactly on index 1 (lo == hi), so no interpolation is needed: v[1] = 2.0.",
    ),
)


# ---------------------------------------------------------------------------
# Composite cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositeCase:
    """One hand-computed :func:`weighted_composite` case."""

    label: str
    composite_name: str
    components: dict[str, float | None]
    expected: float | None
    note: str


COMPOSITE_CASES: tuple[CompositeCase, ...] = (
    CompositeCase(
        "ev_readiness_full",
        "ev_readiness_score",
        {
            "charger_density_score": 80.0,
            "charger_accessibility_score": 60.0,
            "population_adjusted_coverage_score": 40.0,
        },
        62.0,
        "0.40*80 + 0.30*60 + 0.30*40 = 32 + 18 + 12 = 62.0.",
    ),
    CompositeCase(
        "ev_readiness_missing_accessibility",
        "ev_readiness_score",
        {
            "charger_density_score": 80.0,
            "charger_accessibility_score": None,
            "population_adjusted_coverage_score": 40.0,
        },
        None,
        "One required component is None, so the composite is None -- the "
        "0.40 and 0.30 weights of the other two are NOT redistributed to "
        "cover the missing 0.30.",
    ),
    CompositeCase(
        "ev_readiness_measured_zero_density",
        "ev_readiness_score",
        {
            "charger_density_score": 0.0,
            "charger_accessibility_score": 60.0,
            "population_adjusted_coverage_score": 40.0,
        },
        30.0,
        "A measured 0.0 for charger_density_score is a value, not a missing "
        "input: 0.40*0 + 0.30*60 + 0.30*40 = 0 + 18 + 12 = 30.0 (a real "
        "number, not None).",
    ),
    CompositeCase(
        "charger_deficit_from_published_readiness",
        "charger_deficit_score",
        {"ev_readiness_score": 62.0},
        38.0,
        "charger_deficit_score = 100 - ev_readiness_score = 100 - 62.0 = 38.0.",
    ),
    CompositeCase(
        "charger_deficit_none_when_readiness_none",
        "charger_deficit_score",
        {"ev_readiness_score": None},
        None,
        "100 minus a missing value is still missing: None, not 100.0.",
    ),
    CompositeCase(
        "investment_priority_from_published_components",
        "investment_priority_score",
        {
            "charger_deficit_score": 38.0,
            "infrastructure_opportunity_score": 55.5,
        },
        45.0,
        "0.60*38.0 + 0.40*55.5 = 22.8 + 22.2 = 45.0.",
    ),
    CompositeCase(
        "transport_load_full",
        "transport_load_score",
        {
            "traffic_intensity_score": 70.0,
            "traffic_road_density_score": 50.0,
            "road_proximity_score": 20.0,
        },
        57.5,
        "0.60*70 + 0.25*50 + 0.15*20 = 42 + 12.5 + 3 = 57.5.",
    ),
    CompositeCase(
        "grid_readiness_proxy_full",
        "grid_readiness_proxy_score",
        {
            "substation_proximity_score": 80.0,
            "voltage_line_density_score": 60.0,
            "substation_density_score": 40.0,
        },
        65.0,
        "0.45*80 + 0.35*60 + 0.20*40 = 36 + 21 + 8 = 65.0.",
    ),
    CompositeCase(
        "renewable_context_full",
        "renewable_context_score",
        {
            "renewable_capacity_density_score": 50.0,
            "renewable_technology_diversity_score": 90.0,
        },
        62.0,
        "0.70*50 + 0.30*90 = 35 + 27 = 62.0.",
    ),
    CompositeCase(
        "grid_absorption_risk_proxy_full",
        "grid_absorption_risk_proxy_score",
        {
            "local_energy_balance_score": 40.0,
            "renewable_growth_score": 20.0,
            "grid_readiness_proxy_score": 65.0,
        },
        32.8,
        "0.45*40 + 0.30*20 + 0.25*(100-65) = 18 + 6 + 8.75 = 32.75, which "
        "rounds half away from zero to 32.8 (not the banker's-rounding 32.8 "
        "coincidence elsewhere -- here the tie is at the hundredths place "
        "and half-away-from-zero always rounds it up in magnitude).",
    ),
    CompositeCase(
        "infrastructure_opportunity_full",
        "infrastructure_opportunity_score",
        {
            "transport_load_score": 60.0,
            "grid_readiness_proxy_score": 50.0,
            "renewable_context_score": 40.0,
        },
        51.5,
        "0.40*60 + 0.35*50 + 0.25*40 = 24 + 17.5 + 10 = 51.5.",
    ),
    CompositeCase(
        "infrastructure_opportunity_missing_grid_readiness",
        "infrastructure_opportunity_score",
        {
            "transport_load_score": 60.0,
            "grid_readiness_proxy_score": None,
            "renewable_context_score": 40.0,
        },
        None,
        "A composite-of-composites propagates None just like a leaf composite.",
    ),
    # -- rounded-components vs raw-components: the S08 rule changes the answer --
    CompositeCase(
        "ev_readiness_rounded_vs_unrounded_composition",
        "ev_readiness_score",
        {
            "charger_density_score": 62.3,
            "charger_accessibility_score": 60.0,
            "population_adjusted_coverage_score": 40.1,
        },
        55.0,
        "Composing from the PUBLISHED (rounded) components, as the spec "
        "requires: 0.40*62.3 + 0.30*60.0 + 0.30*40.1 = 24.92 + 18.00 + "
        "12.03 = 54.95, which rounds half-away-from-zero to 55.0. The raw "
        "pre-rounding normalize_5_95 outputs that these three components "
        "came from were 62.34, 59.96 and 40.05 (each of which does round "
        "to 62.3 / 60.0 / 40.1 individually); composing from THOSE raw "
        "numbers instead gives 0.40*62.34 + 0.30*59.96 + 0.30*40.05 = "
        "24.936 + 17.988 + 12.015 = 54.939, which rounds to 54.9 -- a "
        "visibly different published score (55.0 vs 54.9) depending on "
        "which stage you round at. 55.0 is correct.",
    ),
    CompositeCase(
        "transport_load_rounded_vs_unrounded_composition",
        "transport_load_score",
        {
            "traffic_intensity_score": 70.1,
            "traffic_road_density_score": 50.0,
            "road_proximity_score": 20.0,
        },
        57.6,
        "Composing from the PUBLISHED (rounded) components: 0.60*70.1 + "
        "0.25*50.0 + 0.15*20.0 = 42.06 + 12.5 + 3.0 = 57.56, which rounds "
        "to 57.6. The raw pre-rounding outputs were 70.051, 49.951 and "
        "19.951 (each still rounds individually to 70.1 / 50.0 / 20.0); "
        "composing from THOSE raw numbers instead gives 0.60*70.051 + "
        "0.25*49.951 + 0.15*19.951 = 42.0306 + 12.48775 + 2.99265 = "
        "57.511, which rounds to 57.5 -- again visibly different (57.6 vs "
        "57.5). 57.6 is correct.",
    ),
)


# ---------------------------------------------------------------------------
# Rank cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankCase:
    """One hand-computed :func:`rank_desc_nulls_last` case."""

    label: str
    scores: tuple[float | None, ...]
    expected: tuple[int, ...]
    note: str


RANK_CASES: tuple[RankCase, ...] = (
    RankCase(
        "strictly_descending_distinct",
        (90.0, 80.0, 70.0),
        (1, 2, 3),
        "No ties: ranks are just the 1-based position in descending order.",
    ),
    RankCase(
        "two_way_tie_then_lower",
        (90.0, 90.0, 70.0),
        (1, 1, 3),
        "Two districts tie for first (rank 1, 1); the next distinct score "
        "skips to rank 3, not 2 -- the tie group's size is not collapsed.",
    ),
    RankCase(
        "three_way_tie_then_lower",
        (80.0, 80.0, 80.0, 50.0),
        (1, 1, 1, 4),
        "A three-way tie all takes rank 1; the next score is rank 4 "
        "(3 rows ranked ahead of it).",
    ),
    RankCase(
        "all_scores_equal",
        (60.0, 60.0, 60.0),
        (1, 1, 1),
        "Every row ties, so every row is rank 1.",
    ),
    RankCase(
        "ties_not_adjacent_in_input_order",
        (50.0, 70.0, 70.0, 50.0, 90.0),
        (4, 2, 2, 4, 1),
        "Ranking does not depend on input order: sorted descending this is "
        "90, 70, 70, 50, 50 -> ranks 1, 2, 2, 4, 4, then mapped back to "
        "the original positions (50.0, 70.0, 70.0, 50.0, 90.0).",
    ),
    RankCase(
        "mixed_none_and_real_scores",
        (70.0, None, 90.0, None, 80.0),
        (3, 4, 1, 4, 2),
        "Non-null scores rank first by value (90 -> 1, 80 -> 2, 70 -> 3); "
        "both None entries share rank 4, the rank immediately following "
        "the 3 ranked rows -- NULLS LAST, and NULLs tie with each other.",
    ),
    RankCase(
        "all_none",
        (None, None, None),
        (1, 1, 1),
        "With zero non-null rows, the shared null rank is "
        "len(non_null) + 1 = 0 + 1 = 1: every row is rank 1.",
    ),
)


# ---------------------------------------------------------------------------
# Edge notes
# ---------------------------------------------------------------------------

EDGE_NOTES: tuple[str, ...] = (
    "Equal (degenerate) lower and upper bounds always normalize to exactly "
    "50.0, in both the normal and inverse directions, and regardless of "
    "the metric's own value -- this is a short-circuit, not a division by "
    "a near-zero denominator.",
    "A composite's weights are fixed and are never redistributed over "
    "whichever components happen to be present: if any required "
    "component is None, the whole composite is None.",
    "A measured 0.0 is a real value and must be used as 0.0 in a "
    "weighted sum; it must never be treated as a missing/None input.",
    "Every PUBLISHED score (component and composite alike) is rounded to "
    "one decimal place using half-away-from-zero rounding, not Python's "
    "banker's-rounding round(). A composite is the weighted sum of its "
    "already-rounded components, and that sum is rounded again -- "
    "composing instead from pre-rounding raw values can and does produce "
    "a visibly different published number.",
    "charger_deficit_score and the (100 - grid_readiness_proxy_score) "
    "term inside grid_absorption_risk_proxy_score both invert an "
    "already-published (rounded) score by subtracting it from 100; a "
    "None input still propagates through that subtraction as None, "
    "never as 100.0.",
    "In RANK() OVER (ORDER BY score DESC NULLS LAST), tied scores share "
    "one rank and the next distinct (lower) score skips ranks by the "
    "size of the tie group (e.g. 1, 1, 3, never 1, 1, 2). Rows with a "
    "None score sort after every real score and all share the single "
    "rank that immediately follows the ranked rows.",
    "The project's normalization bounds are always the baseline's own "
    "5th and 95th percentiles (percentile_cont over all 53 districts of "
    "the official BASELINE); scenario scores reuse those baseline bounds "
    "unchanged rather than computing fresh bounds from the scenario.",
)
