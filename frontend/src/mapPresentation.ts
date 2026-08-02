export type MapScoreMetric =
  | "investmentPriorityScore"
  | "evReadinessScore"
  | "chargerDeficitScore"
  | "infrastructureOpportunityScore";

export type MapScenarioMode = "baseline" | "scenario" | "change";

export const OVERLAY_ORDER = [
  "districts",
  "regionalRoads",
  "autobahns",
  "officialStations",
  "proposedStations"
] as const;

export const REGIONAL_ROAD_STYLE = {
  color: "#93c5fd",
  weight: 1.35,
  opacity: 0.68
} as const;

export const AUTOBAHN_STYLE = {
  color: "#fb7185",
  weight: 3.2,
  opacity: 0.92
} as const;

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    };
    return entities[character];
  });
}

export function scoreColor(
  score: number,
  mode: MapScenarioMode,
  metric: MapScoreMetric
): string {
  if (mode === "change") {
    const improvement = metric === "chargerDeficitScore" || metric === "investmentPriorityScore"
      ? -score
      : score;
    if (improvement >= 10) return "#2563eb";
    if (improvement > 0) return "#38bdf8";
    if (improvement === 0) return "#64748b";
    if (improvement > -10) return "#c026d3";
    return "#be123c";
  }
  if (score >= 80) return "#f97316";
  if (score >= 65) return "#eab308";
  if (score >= 50) return "#06b6d4";
  if (score >= 35) return "#3b82f6";
  return "#6d28d9";
}

export function officialStationStyle(powerKw: number, zoom = 9) {
  const highPower = powerKw >= 150;
  const scale = zoom <= 7 ? 0.52 : zoom === 8 ? 0.72 : 1;
  return {
    pane: "stations",
    radius: (highPower ? 4 : 2.9) * scale,
    color: highPower ? "#fff7c2" : "#e6fbff",
    weight: (highPower ? 1.05 : 0.8) * Math.max(scale, 0.65),
    fillColor: highPower ? "#facc15" : "#22d3ee",
    fillOpacity: highPower ? 0.98 : 0.9
  };
}

export function operatorTooltip(operator: string | undefined): string {
  const label = operator?.trim() || "Operator not specified";
  return `<strong>${escapeHtml(label)}</strong><span>Charging-station operator</span>`;
}
