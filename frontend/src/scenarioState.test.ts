import { describe, expect, it } from "vitest";

import { projectScenarioProperties } from "./scenarioState";


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
  it("projects baseline fields into the canonical dashboard fields", () => {
    expect(projectScenarioProperties(metrics, "baseline")).toMatchObject({
      chargers_total: 10,
      charging_points_total: 20,
      fast_chargers_total: 4,
      ev_readiness_score: 40,
      charging_supply_score: 40,
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

  it("projects signed change values for the comparison choropleth", () => {
    expect(projectScenarioProperties(metrics, "change")).toMatchObject({
      chargers_total: 2,
      charging_points_total: 6,
      fast_chargers_total: 1,
      ev_readiness_score: 12.5,
      charging_supply_score: 12.5,
      charger_deficit_score: -12.5,
      investment_priority_score: -7.5,
      infrastructure_opportunity_score: 0,
      priority_rank: 5,
      scenario_view_mode: "change"
    });
  });
});
