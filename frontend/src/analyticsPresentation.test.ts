import { describe, expect, it } from "vitest";

import {
  detailContext,
  finiteNumber,
  fastShareChange,
  fastShareFromCounts,
  formatCoveragePercentage,
  publishedPriorityRank,
  signedValue,
  sortDisplayRows,
  statewideFastShare,
  publishedScoreModelTerms
} from "./analyticsPresentation";

describe("analytical presentation fixtures", () => {
  it("calculates a statewide fast share from counts, rather than averaging district percentages", () => {
    // Independent fixture: 1/2 (50%) and 9/90 (10%) must aggregate to 10/92,
    // not the unweighted 30% district average.
    const share = statewideFastShare([
      { chargers_total: 2, fast_chargers_total: 1, unknown_power_chargers_total: 0 },
      { chargers_total: 90, fast_chargers_total: 9, unknown_power_chargers_total: 0 }
    ]);
    expect(share).toMatchObject({ available: true, percent: 1000 / 92, fast: 10, total: 92 });
  });

  it("keeps zero totals and unknown maximum-point-power classifications unavailable", () => {
    expect(fastShareFromCounts({ total: 0, fast: 0, unknownPower: 0 }))
      .toMatchObject({ available: false, reason: "zero_total" });
    expect(fastShareFromCounts({ total: 10, fast: 4, unknownPower: 1 }))
      .toMatchObject({ available: false, reason: "incomplete_classification" });
    expect(fastShareFromCounts({ total: undefined, fast: 4, unknownPower: 0 }))
      .toMatchObject({ available: false, reason: "missing_counts" });
  });

  it("rejects null count inputs in every view instead of coercing them to zero", () => {
    expect(statewideFastShare([{ chargers_total: 10, fast_chargers_total: 4, unknown_power_chargers_total: null }]))
      .toMatchObject({ available: false, reason: "missing_counts" });
    expect(statewideFastShare([{
      scenario_chargers_total: 10, scenario_fast_chargers_total: null, scenario_unknown_power_chargers_total: 0
    }], "scenario_"))
      .toMatchObject({ available: false, reason: "missing_counts" });
    expect(fastShareChange([{
      baseline_chargers_total: 10, baseline_fast_chargers_total: null, baseline_unknown_power_chargers_total: 0,
      scenario_chargers_total: 10, scenario_fast_chargers_total: 4, scenario_unknown_power_chargers_total: 0
    }])).toMatchObject({ available: false });
    expect(finiteNumber("")).toBeNull();
    expect(finiteNumber(false)).toBeNull();
  });

  it("shows signed scenario-minus-baseline percentage-point change", () => {
    const change = fastShareChange([
      {
        baseline_chargers_total: 20, baseline_fast_chargers_total: 4, baseline_unknown_power_chargers_total: 0,
        scenario_chargers_total: 25, scenario_fast_chargers_total: 7, scenario_unknown_power_chargers_total: 0
      }
    ]);
    expect(change).toMatchObject({ available: true, percentagePoints: 8 });
    expect(signedValue(8)).toBe("+8.0");
    expect(signedValue(-8)).toBe("-8.0");
  });

  it("orders change rows by signed delta and preserves deterministic SQL-tie display order", () => {
    const rows = sortDisplayRows([
      { value: -12, nutsCode: "DEA04", item: "negative" },
      { value: 0, nutsCode: "DEA03", item: "zero" },
      { value: 6, nutsCode: "DEA02", item: "positive-b" },
      { value: 6, nutsCode: "DEA01", item: "positive-a" }
    ], true);
    expect(rows.map(({ item }) => item)).toEqual(["positive-a", "positive-b", "zero", "negative"]);
  });

  it("retains known baseline-only context in scenario and difference detail panels", () => {
    const current = { infrastructure_opportunity_score: 0, transport_load_score: undefined };
    const baseline = { infrastructure_opportunity_score: 64.4, transport_load_score: 46 };
    expect(detailContext(current, baseline, true)).toEqual(baseline);
    expect(detailContext(current, baseline, false)).toEqual(current);
  });

  it("formats canonical coverage ratios as percentages and never ranks unavailable priority", () => {
    expect(formatCoveragePercentage(1)).toBe("100%");
    expect(formatCoveragePercentage(0.5)).toBe("50%");
    expect(formatCoveragePercentage(0)).toBe("0%");
    expect(formatCoveragePercentage(null)).toBe("—");
    expect(publishedPriorityRank(null, 6)).toBeNull();
    expect(publishedPriorityRank(72, 6)).toBe(6);
  });

  it("reads published normalization bounds from either canonical delivery path", () => {
    const term = { measure: "charging_points_per_km2", lower_bound: 0.29, upper_bound: 7.09 };
    expect(publishedScoreModelTerms({ score_model: [term] })).toEqual([term]);
    expect(publishedScoreModelTerms({ features: [{ properties: term }] })).toEqual([term]);
    expect(publishedScoreModelTerms({ features: [] })).toEqual([]);
  });
});
