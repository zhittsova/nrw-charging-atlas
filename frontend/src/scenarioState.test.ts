import { describe, expect, it } from "vitest";

import { projectScenarioProperties, scenarioMetricCoverageIssue, stationTotalForView } from "./scenarioState";


const metrics = {
  nuts_code: "DEA01",
  baseline_chargers_total: 10,
  scenario_chargers_total: 12,
  chargers_total_delta: 2,
  baseline_charging_points_total: 20,
  scenario_charging_points_total: 26,
  charging_points_total_delta: 6,
  baseline_fast_chargers_total: 4,
  scenario_fast_chargers_total: 5,
  fast_chargers_total_delta: 1,
  baseline_ev_readiness_score: 40,
  scenario_ev_readiness_score: 52.5,
  ev_readiness_score_delta: 12.5,
  baseline_charger_deficit_score: 60,
  scenario_charger_deficit_score: 47.5,
  charger_deficit_score_delta: -12.5,
  baseline_investment_priority_score: 70,
  scenario_investment_priority_score: 62.5,
  investment_priority_score_delta: -7.5,
  infrastructure_opportunity_score: 55,
  baseline_priority_rank: 3,
  scenario_priority_rank: 5
};

describe("scenario display state", () => {
  it("requires one scenario metric for every canonical baseline district", () => {
    const baseline = [{ properties: { nuts_code: "DEA01" } }, { properties: { nuts_code: "DEA02" } }];
    expect(scenarioMetricCoverageIssue([{ properties: { nuts_code: "DEA01" } }], baseline)).toContain("1 missing");
    expect(scenarioMetricCoverageIssue([
      { properties: { nuts_code: "DEA01" } },
      { properties: { nuts_code: "DEA01" } }
    ], baseline)).toContain("duplicate");
    expect(scenarioMetricCoverageIssue([
      { properties: { nuts_code: "DEA01" } },
      { properties: { nuts_code: "DEA02" } }
    ], baseline)).toBeNull();
  });

  it("keeps an unavailable official-station total distinct from a real zero", () => {
    expect(stationTotalForView(0, 3, "baseline", false)).toBeNull();
    expect(stationTotalForView(0, 3, "scenario", false)).toBeNull();
    expect(stationTotalForView(0, 3, "scenario", true)).toBe(3);
    expect(stationTotalForView(0, 0, "baseline", true)).toBe(0);
  });
  it("projects baseline fields into the canonical dashboard fields", () => {
    expect(projectScenarioProperties(metrics, "baseline")).toMatchObject({
      chargers_total: 10,
      charging_points_total: 20,
      fast_chargers_total: 4,
      ev_readiness_score: 40,
      charger_deficit_score: 60,
      investment_priority_score: 70,
      priority_rank: 3,
      scenario_view_mode: "baseline"
    });
  });

  it("projects scenario fields without discarding comparison fields", () => {
    const projected = projectScenarioProperties(metrics, "scenario");

    expect(projected).toMatchObject({
      chargers_total: 12,
      charging_points_total: 26,
      fast_chargers_total: 5,
      ev_readiness_score: 52.5,
      charger_deficit_score: 47.5,
      investment_priority_score: 62.5,
      priority_rank: 5,
      baseline_investment_priority_score: 70,
      scenario_view_mode: "scenario"
    });
  });

  it("keeps unknown baseline-only context unavailable in change mode", () => {
    const projected = projectScenarioProperties({ ...metrics, infrastructure_opportunity_score: null }, "change");
    expect(projected.infrastructure_opportunity_score).toBeNull();
  });

  it("projects signed change values for the comparison choropleth", () => {
    expect(projectScenarioProperties(metrics, "change")).toMatchObject({
      chargers_total: 2,
      charging_points_total: 6,
      fast_chargers_total: 1,
      ev_readiness_score: 12.5,
      charger_deficit_score: -12.5,
      investment_priority_score: -7.5,
      infrastructure_opportunity_score: 0,
      priority_rank: 5,
      scenario_view_mode: "change"
    });
  });
});
