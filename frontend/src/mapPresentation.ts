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
  "renewableAssets",
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
  weight: 2.2,
  opacity: 0.88
} as const;

type AutobahnProperties = {
  ref?: string;
  name?: string;
  source_id?: string;
  [key: string]: unknown;
};

type AutobahnFeature = GeoJSON.Feature<GeoJSON.Geometry, AutobahnProperties>;

function lineLength(coordinates: GeoJSON.Position[]): number {
  let length = 0;
  for (let index = 1; index < coordinates.length; index += 1) {
    const [previousLongitude, previousLatitude] = coordinates[index - 1];
    const [longitude, latitude] = coordinates[index];
    const longitudeScale = Math.cos(((latitude + previousLatitude) / 2) * Math.PI / 180);
    length += Math.hypot(
      (longitude - previousLongitude) * longitudeScale,
      latitude - previousLatitude
    );
  }
  return length;
}

function autobahnFeatureLength(feature: AutobahnFeature): number {
  if (feature.geometry.type === "LineString") return lineLength(feature.geometry.coordinates);
  if (feature.geometry.type === "MultiLineString") {
    return feature.geometry.coordinates.reduce((total, coordinates) => total + lineLength(coordinates), 0);
  }
  return 0;
}

/** Keeps complete high-value Autobahn routes at wider views instead of broken sampled segments. */
export function selectAutobahnFeaturesForZoom<T extends AutobahnFeature>(
  features: T[],
  zoom: number
): T[] {
  const routeLimit = zoom <= 6 ? 4 : zoom === 7 ? 7 : zoom === 8 ? 12 : 0;
  if (!routeLimit) return features;

  const routeLengths = new Map<string, number>();
  features.forEach((feature) => {
    const route = feature.properties?.ref?.trim();
    if (!route) return;
    routeLengths.set(route, (routeLengths.get(route) ?? 0) + autobahnFeatureLength(feature));
  });
  const selectedRoutes = new Set(
    [...routeLengths.entries()]
      .sort(([leftRoute, leftLength], [rightRoute, rightLength]) =>
        rightLength - leftLength || leftRoute.localeCompare(rightRoute))
      .slice(0, routeLimit)
      .map(([route]) => route)
  );
  return features.filter((feature) => selectedRoutes.has(feature.properties?.ref?.trim() ?? ""));
}

type PrioritizableStationProperties = {
  id?: string;
  power_kw?: number;
  charging_points?: number;
  connectors?: number;
  [key: string]: unknown;
};

type PrioritizableStationFeature = GeoJSON.Feature<GeoJSON.Geometry, PrioritizableStationProperties>;

function finiteNumber(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

type RenewableProperties = {
  source_id?: string;
  technology?: string;
  capacity_mw?: number;
  [key: string]: unknown;
};

type RenewableFeature = GeoJSON.Feature<GeoJSON.Geometry, RenewableProperties>;

export const RENEWABLE_TECHNOLOGY_COLORS: Record<string, string> = {
  Windenergie: "#38bdf8",
  "Photovoltaik Freifläche": "#facc15",
  "Photovoltaik Bauliche": "#facc15"
};

export const RENEWABLE_LEGEND_ITEMS = [
  { label: "Solar energy", color: "#facc15" },
  { label: "Wind energy", color: "#38bdf8" }
] as const;

export function isDisplayedRenewableTechnology(technology: string | undefined): boolean {
  return technology === "Windenergie" || technology?.startsWith("Photovoltaik") === true;
}

export function renewableTechnologyLabel(technology: string | undefined): string {
  const labels: Record<string, string> = {
    Windenergie: "Wind energy",
    "Photovoltaik Freifläche": "Ground-mounted solar",
    "Photovoltaik Bauliche": "Rooftop solar",
    Biomasse: "Biomass",
    Wasserkraft: "Hydropower",
    Deponiegas: "Landfill gas",
    Grubengas: "Mine gas",
    Klärgas: "Sewage gas"
  };
  return labels[technology ?? ""] ?? (technology?.trim() || "Renewable energy");
}

export function renewableAssetStyle(technology: string | undefined, capacityMw: number, zoom = 9) {
  const scale = zoom <= 7 ? 0.72 : zoom === 8 ? 0.86 : 1;
  const radius = Math.min(6.2, 2.3 + Math.log10(Math.max(capacityMw, 0) + 1) * 1.45) * scale;
  return {
    pane: "renewableAssets",
    radius,
    color: "#f8fafc",
    weight: 0.7,
    fillColor: RENEWABLE_TECHNOLOGY_COLORS[technology ?? ""] ?? "#94a3b8",
    fillOpacity: 0.88
  };
}

function renewableTechnologyGroup(technology: string | undefined): string {
  if (technology?.startsWith("Photovoltaik")) return "solar";
  return "wind";
}

/** Keeps the highest-capacity asset per technology group and geographic cell. */
export function selectRenewableFeaturesForZoom<T extends RenewableFeature>(
  features: T[],
  zoom: number
): T[] {
  const cellSize = zoom <= 6
    ? 0.75
    : zoom === 7
      ? 0.45
      : zoom === 8
        ? 0.25
        : zoom === 9
          ? 0.12
          : zoom === 10
            ? 0.06
            : zoom === 11
              ? 0.03
              : 0;
  if (!cellSize) return features;

  const selected = new Map<string, T>();
  features.forEach((feature) => {
    if (feature.geometry.type !== "Point") return;
    const [longitude, latitude] = feature.geometry.coordinates;
    const properties = feature.properties ?? {};
    const key = [
      Math.floor(longitude / cellSize),
      Math.floor(latitude / cellSize),
      renewableTechnologyGroup(properties.technology)
    ].join(":");
    const current = selected.get(key);
    const candidateCapacity = finiteNumber(properties.capacity_mw);
    const currentCapacity = finiteNumber(current?.properties?.capacity_mw);
    const candidateId = String(properties.source_id ?? "");
    const currentId = String(current?.properties?.source_id ?? "");
    if (!current || candidateCapacity > currentCapacity
      || (candidateCapacity === currentCapacity && candidateId.localeCompare(currentId) < 0)) {
      selected.set(key, feature);
    }
  });
  return [...selected.values()];
}

function stationPriority(feature: PrioritizableStationFeature): [number, number, number, string] {
  const properties = feature.properties ?? {};
  const power = finiteNumber(properties.power_kw);
  const points = Math.max(
    finiteNumber(properties.charging_points),
    finiteNumber(properties.connectors),
    1
  );
  return [power * points, power, points, String(properties.id ?? "")];
}

function hasHigherStationPriority(
  candidate: PrioritizableStationFeature,
  current: PrioritizableStationFeature
): boolean {
  const candidatePriority = stationPriority(candidate);
  const currentPriority = stationPriority(current);
  for (let index = 0; index < candidatePriority.length - 1; index += 1) {
    if (candidatePriority[index] !== currentPriority[index]) {
      return candidatePriority[index] > currentPriority[index];
    }
  }
  return candidatePriority[3].localeCompare(currentPriority[3]) < 0;
}

/**
 * Keeps one high-value station per geographic cell at wider map views.
 * The fixed grid makes the selection stable while the user pans the map.
 */
export function selectOfficialStationsForZoom<T extends PrioritizableStationFeature>(
  features: T[],
  zoom: number
): T[] {
  const cellSize = zoom <= 6 ? 0.75 : zoom === 7 ? 0.45 : zoom === 8 ? 0.25 : zoom === 9 ? 0.12 : 0;
  if (!cellSize) return features;

  const selected = new Map<string, T>();
  const withoutPointGeometry: T[] = [];
  features.forEach((feature) => {
    if (feature.geometry.type !== "Point") {
      withoutPointGeometry.push(feature);
      return;
    }
    const [longitude, latitude] = feature.geometry.coordinates;
    const key = `${Math.floor(longitude / cellSize)}:${Math.floor(latitude / cellSize)}`;
    const current = selected.get(key);
    if (!current || hasHigherStationPriority(feature, current)) selected.set(key, feature);
  });
  return [...selected.values(), ...withoutPointGeometry];
}

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
    color: highPower ? "#fff1f2" : "#fce7f3",
    weight: (highPower ? 1.05 : 0.8) * Math.max(scale, 0.65),
    fillColor: highPower ? "#f472b6" : "#ec4899",
    fillOpacity: highPower ? 0.98 : 0.9
  };
}

export function operatorTooltip(operator: string | undefined): string {
  const label = operator?.trim() || "Operator not specified";
  return `<strong>${escapeHtml(label)}</strong><span>Charging-station operator</span>`;
}
