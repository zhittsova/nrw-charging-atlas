export type ScenarioViewMode = "baseline" | "scenario" | "change";

type ScenarioProperties = Record<string, unknown>;

const MODE_PREFIX: Record<Exclude<ScenarioViewMode, "change">, string> = {
  baseline: "baseline_",
  scenario: "scenario_"
};

const CANONICAL_METRICS = [
  "chargers_total",
  "charging_points_total",
  "fast_chargers_total",
  "normal_chargers_total",
  "unknown_power_chargers_total",
  "chargers_per_km2",
  "charging_points_per_100k_population",
  "distance_to_nearest_charger_m",
  "charger_density_score",
  "charger_accessibility_score",
  "population_adjusted_coverage_score",
  "ev_readiness_score",
  "charger_deficit_score",
  "investment_priority_score"
] as const;

export function projectScenarioProperties(
  properties: ScenarioProperties,
  mode: ScenarioViewMode
): ScenarioProperties {
  const projected: ScenarioProperties = { ...properties, scenario_view_mode: mode };

  for (const metric of CANONICAL_METRICS) {
    const source = mode === "change" ? `${metric}_delta` : `${MODE_PREFIX[mode]}${metric}`;
    projected[metric] = properties[source];
  }
  projected.charging_supply_score = projected.ev_readiness_score;
  projected.infrastructure_opportunity_score = mode === "change"
    ? 0
    : properties.infrastructure_opportunity_score;
  projected.priority_rank = mode === "baseline"
    ? properties.baseline_priority_rank
    : properties.scenario_priority_rank;

  return projected;
}
