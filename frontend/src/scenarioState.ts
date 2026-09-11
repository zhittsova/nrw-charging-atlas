export type ScenarioViewMode = "baseline" | "scenario" | "change";

type ScenarioProperties = Record<string, unknown>;
type ScenarioMetricFeature = { properties: ScenarioProperties | null };

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

/** Returns a user-safe reason when scenario metrics cannot safely replace the baseline. */
export function scenarioMetricCoverageIssue(
  scenarioFeatures: readonly ScenarioMetricFeature[],
  baselineFeatures: readonly ScenarioMetricFeature[]
): string | null {
  const baselineCodes = new Set(baselineFeatures.map((feature) => feature.properties?.nuts_code).filter(
    (code): code is string => typeof code === "string" && code.length > 0
  ));
  const scenarioCodes = scenarioFeatures.map((feature) => feature.properties?.nuts_code).filter(
    (code): code is string => typeof code === "string" && code.length > 0
  );
  const scenarioCodeSet = new Set(scenarioCodes);
  if (!baselineCodes.size) return "baseline district coverage is unavailable";
  if (scenarioFeatures.length !== scenarioCodes.length || scenarioCodeSet.size !== scenarioCodes.length) {
    return "scenario metrics have missing or duplicate district identifiers";
  }
  const missing = [...baselineCodes].filter((code) => !scenarioCodeSet.has(code));
  const unexpected = scenarioCodes.filter((code) => !baselineCodes.has(code));
  if (missing.length || unexpected.length) {
    return `scenario metrics do not cover the canonical baseline districts (${missing.length} missing, ${unexpected.length} unexpected)`;
  }
  return null;
}

/** A failed official-station read is unknown, not an observed zero. */
export function stationTotalForView(
  officialStationCount: number,
  proposedStationCount: number,
  mode: ScenarioViewMode,
  officialStationDataAvailable: boolean
): number | null {
  if (!officialStationDataAvailable) return null;
  if (mode === "baseline") return officialStationCount;
  if (mode === "scenario") return officialStationCount + proposedStationCount;
  return proposedStationCount;
}

export function projectScenarioProperties(
  properties: ScenarioProperties,
  mode: ScenarioViewMode
): ScenarioProperties {
  const projected: ScenarioProperties = { ...properties, scenario_view_mode: mode };

  for (const metric of CANONICAL_METRICS) {
    const source = mode === "change" ? `${metric}_delta` : `${MODE_PREFIX[mode]}${metric}`;
    projected[metric] = properties[source];
  }
  // Infrastructure context is baseline-only. A zero change is meaningful only
  // when the canonical baseline value exists; unknown context stays unknown.
  projected.infrastructure_opportunity_score = mode === "change"
    ? (properties.infrastructure_opportunity_score === null || properties.infrastructure_opportunity_score === undefined
      ? null
      : 0)
    : properties.infrastructure_opportunity_score;
  projected.priority_rank = mode === "baseline"
    ? properties.baseline_priority_rank
    : properties.scenario_priority_rank;

  return projected;
}
