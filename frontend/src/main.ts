import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "../style.css";
import { createScenarioClient, type GeoJsonFeatureCollection } from "./scenarioClient";
import { projectScenarioProperties, type ScenarioViewMode } from "./scenarioState";

(window as Window & { __energyAppBooted?: boolean }).__energyAppBooted = true;

type ScoreMetric =
  | "investmentPriorityScore"
  | "evReadinessScore"
  | "chargerDeficitScore"
  | "infrastructureOpportunityScore";

type RegionProperties = {
  id?: string;
  name?: string;
  district_name?: string;
  nuts_code?: string;
  district_code?: string;
  region?: string;
  region_abbr?: string;
  box?: [number, number, number, number];
  stationCount?: number;
  chargers_total?: number;
  charging_points_total?: number;
  fast_chargers?: number;
  normal_chargers?: number;
  chargers_per_km2?: number;
  evReadinessScore?: number;
  ev_readiness_score?: number;
  chargingSupplyScore?: number;
  charging_supply_score?: number;
  chargerDeficitScore?: number;
  charger_deficit_score?: number;
  investmentPriorityScore?: number;
  investment_priority_score?: number;
  infrastructureOpportunityScore?: number;
  infrastructure_opportunity_score?: number;
  priorityRank?: number;
  priority_rank?: number;
  priorityTier?: string;
  priority_tier?: string;
  dataQualityScore?: number;
  data_quality_score?: number;
  dataQualityFlag?: string;
  data_quality_flag?: string;
  scenario_view_mode?: ScenarioViewMode;
  [key: string]: unknown;
};

type StationProperties = {
  id?: string;
  name?: string;
  operator?: string;
  address?: string;
  district_name?: string;
  region?: string;
  state?: string;
  power_kw?: number;
  charging_points?: number;
  connectors?: number;
  status?: string;
  [key: string]: unknown;
};

type Feature<T> = GeoJSON.Feature<GeoJSON.Geometry, T>;
type FeatureCollection<T> = GeoJSON.FeatureCollection<GeoJSON.Geometry, T>;

type RuntimeConfig = {
  geonodeBaseUrl: string;
  geoserverBaseUrl: string;
  geonodeStationsLayer: string;
  geonodeRegionsLayer: string;
  scenarioMetricsLayer: string;
  proposedChargersLayer: string;
  scenarioFeaturePrefix: string;
  scenarioNamespaceUri: string;
};

const NRW_CENTER: L.LatLngExpression = [51.43, 7.66];
const NRW_BOUNDS: L.LatLngBoundsExpression = [
  [50.32, 5.86],
  [52.53, 9.47]
];

const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  geonodeBaseUrl: "http://localhost:8000",
  geoserverBaseUrl: "http://localhost:8080",
  geonodeStationsLayer: "geonode:nrw_ev_charging_stations",
  geonodeRegionsLayer: "geonode:nrw_nuts3_districts",
  scenarioMetricsLayer: "nrw:nrw_ev_scenario_metrics",
  proposedChargersLayer: "nrw:proposed_chargers",
  scenarioFeaturePrefix: "nrw",
  scenarioNamespaceUri: "https://nrw.local/scenario"
};

const runtimeConfig = (() => {
  const config = (window as Window & { __energyAppConfig?: Partial<RuntimeConfig> }).__energyAppConfig;
  return { ...DEFAULT_RUNTIME_CONFIG, ...config };
})();

const scenarioClient = createScenarioClient({
  wfsUrl: `${trimTrailingSlash(runtimeConfig.geoserverBaseUrl)}/geoserver/ows`,
  proposedLayer: runtimeConfig.proposedChargersLayer,
  scenarioMetricsLayer: runtimeConfig.scenarioMetricsLayer,
  featureType: {
    prefix: runtimeConfig.scenarioFeaturePrefix,
    namespaceUri: runtimeConfig.scenarioNamespaceUri,
    typeName: runtimeConfig.proposedChargersLayer.split(":").pop() ?? "proposed_chargers"
  }
});

const datasets = [
  {
    id: "stations",
    title: "NRW EV Charging Stations",
    type: "Point GeoJSON",
    file: "data/nrw_charging_stations_sample.geojson",
    service: "GeoServer WFS: geonode:nrw_ev_charging_stations",
    note: "Bundesnetzagentur Ladesaeulenregister filtered to Nordrhein-Westfalen"
  },
  {
    id: "regions",
    title: "NRW NUTS-3 Districts",
    type: "Polygon GeoJSON",
    file: "data/nrw_regions_sample.geojson",
    service: "GeoServer WMS/WFS: geonode:nrw_nuts3_districts",
    note: "Eurostat GISCO NUTS-3 2024 filtered by NUTS prefix DEA"
  },
  {
    id: "metrics",
    title: "NRW Priority Metrics",
    type: "Derived metrics",
    file: "data/processed/nrw_district_metrics.geojson",
    service: "GeoNode REST API and PostGIS materialized views",
    note: "PostGIS combines chargers, population, transport load, grid readiness and renewable context"
  }
];

const scoreLabels: Record<ScoreMetric, string> = {
  investmentPriorityScore: "Investment Priority",
  evReadinessScore: "EV Readiness",
  chargerDeficitScore: "Charger Deficit",
  infrastructureOpportunityScore: "Infrastructure Opportunity"
};

const scoreKeys: Record<ScoreMetric, string[]> = {
  investmentPriorityScore: ["investmentPriorityScore", "investment_priority_score"],
  evReadinessScore: ["evReadinessScore", "ev_readiness_score", "chargingSupplyScore", "charging_supply_score"],
  chargerDeficitScore: ["chargerDeficitScore", "charger_deficit_score"],
  infrastructureOpportunityScore: ["infrastructureOpportunityScore", "infrastructure_opportunity_score"]
};

let activeScore: ScoreMetric = "investmentPriorityScore";
let activeRanking: ScoreMetric = "investmentPriorityScore";
let scenarioViewMode: ScenarioViewMode = "baseline";
let regionFeatures: Feature<RegionProperties>[] = [];
let scenarioMetricFeatures: Feature<RegionProperties>[] = [];
let officialStations: FeatureCollection<StationProperties> = { type: "FeatureCollection", features: [] };
let proposedStations: FeatureCollection<StationProperties> = { type: "FeatureCollection", features: [] };
let selectedRegionId: string | null = null;
let selectedLayer: L.Layer | null = null;
let pendingLocation: L.LatLng | null = null;
let awaitingMapClick = false;

const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(NRW_CENTER, 7);
map.createPane("regions");
map.createPane("stations");
map.createPane("proposedStations");
map.getPane("regions")!.style.zIndex = "410";
map.getPane("stations")!.style.zIndex = "460";
map.getPane("proposedStations")!.style.zIndex = "480";

const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap"
});

const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 20,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
}).addTo(map);

const stationLayer = L.geoJSON(undefined, {
  pane: "stations",
  pointToLayer: (feature, latlng) => {
    const p = feature.properties as StationProperties;
    const power = numericValue(p, ["power_kw"], 22);
    return L.circleMarker(latlng, {
      radius: power >= 150 ? 2.8 : 1.8,
      color: power >= 150 ? "#f97316" : "#0ea5e9",
      weight: 0.55,
      fillColor: power >= 150 ? "#fb923c" : "#38bdf8",
      fillOpacity: 0.72
    });
  },
  onEachFeature: (feature, layer) => {
    const p = feature.properties as StationProperties;
    layer.bindPopup(`
      <div class="station-popup">
        <h3>${escapeHtml(textValue(p, ["name"], "Charging station"))}</h3>
        <p><strong>Operator:</strong> ${escapeHtml(textValue(p, ["operator"], "unknown"))}</p>
        <p><strong>District:</strong> ${escapeHtml(textValue(p, ["district_name", "region"], "NRW"))}</p>
        <p><strong>Address:</strong> ${escapeHtml(textValue(p, ["address"], "not available"))}</p>
        <p><strong>Power:</strong> ${formatNumber(numericValue(p, ["power_kw"], 0))} kW</p>
        <p><strong>Charging points:</strong> ${formatNumber(numericValue(p, ["charging_points", "connectors"], 0))}</p>
      </div>
    `);
  }
}).addTo(map);

const proposedStationLayer = L.geoJSON(undefined, {
  pane: "proposedStations",
  pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
    radius: 6,
    color: "#f4f1ff",
    weight: 1.5,
    fillColor: "#8b4dff",
    fillOpacity: 0.92
  }),
  onEachFeature: (feature, layer) => {
    const p = feature.properties as StationProperties;
    const popup = document.createElement("div");
    popup.className = "station-popup proposed-popup";
    popup.innerHTML = `
      <h3>${escapeHtml(textValue(p, ["name"], "Proposed station"))}</h3>
      <p><strong>Status:</strong> Proposed scenario</p>
      <p><strong>District:</strong> ${escapeHtml(textValue(p, ["nuts_code"], "NRW"))}</p>
      <p><strong>Power:</strong> ${formatNumber(numericValue(p, ["power_kw"], 0))} kW</p>
      <p><strong>Charging points:</strong> ${formatNumber(numericValue(p, ["charging_points"], 0))}</p>
    `;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "popup-delete";
    removeButton.textContent = "Remove from scenario";
    removeButton.addEventListener("click", async () => {
      const id = textValue(p, ["id"], "");
      if (id) await removeScenarioStation(id);
    });
    popup.appendChild(removeButton);
    layer.bindPopup(popup);
  }
}).addTo(map);

const regionLayer = L.geoJSON(undefined, {
  pane: "regions",
  style: (feature) => regionStyle(feature as Feature<RegionProperties>),
  onEachFeature: (feature, layer) => {
    const regionFeature = feature as Feature<RegionProperties>;
    layer.on("click", () => selectRegion(regionFeature, layer));
    layer.bindTooltip(() => {
      const p = regionFeature.properties;
      return `${escapeHtml(regionName(p))}: ${formatScore(scoreValue(p, activeScore))} (${scoreLabels[activeScore]})`;
    }, { sticky: true });
  }
}).addTo(map);

L.control
  .layers(
    {
      "Carto Dark": carto,
      OpenStreetMap: osm
    },
    {
      "EV charging stations": stationLayer,
      "Proposed charging stations": proposedStationLayer,
      "NRW NUTS-3 districts": regionLayer
    },
    { collapsed: false }
  )
  .addTo(map);

function setText(id: string, value: string | number): void {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value);
}

function setHeaderStatus(value: string): void {
  const element = document.querySelector(".header-status");
  if (!element) return;
  element.innerHTML = `<span class="status-dot"></span>${escapeHtml(value)}`;
}

function setScenarioStatus(value: string, error = false): void {
  const element = document.getElementById("scenario-status");
  if (!element) return;
  element.textContent = value;
  element.classList.toggle("error", error);
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

function firstValue(properties: Record<string, unknown>, keys: string[]): unknown {
  return keys
    .map((key) => properties[key])
    .find((value) => value !== undefined && value !== null && value !== "");
}

function textValue(properties: Record<string, unknown>, keys: string[], fallback = "-"): string {
  const value = firstValue(properties, keys);
  return value === undefined ? fallback : String(value);
}

function numericValue(properties: Record<string, unknown>, keys: string[], fallback = 0): number {
  const value = firstValue(properties, keys);
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function regionName(properties: RegionProperties): string {
  return textValue(properties, ["district_name", "name", "region_name"], "NRW district");
}

function regionId(properties: RegionProperties): string {
  return textValue(properties, ["id", "nuts_code", "district_code", "name"], regionName(properties));
}

function scoreValue(properties: RegionProperties, metric: ScoreMetric): number {
  return numericValue(properties, scoreKeys[metric], 0);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function formatScore(value: number, signed = scenarioViewMode === "change"): string {
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return signed && value > 0 ? `+${formatted}` : formatted;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function geoserverWfsUrl(typeName: string): string {
  const baseUrl = trimTrailingSlash(runtimeConfig.geoserverBaseUrl);
  const query = new URLSearchParams({
    service: "WFS",
    version: "2.0.0",
    request: "GetFeature",
    typeNames: typeName,
    outputFormat: "application/json"
  });
  return `${baseUrl}/geoserver/ows?${query.toString()}`;
}

function scoreColor(score: number, metric: ScoreMetric = activeScore): string {
  if (scenarioViewMode === "change") {
    const improvement = metric === "chargerDeficitScore" || metric === "investmentPriorityScore"
      ? -score
      : score;
    if (improvement >= 10) return "#0f766e";
    if (improvement > 0) return "#4d9f75";
    if (improvement === 0) return "#64748b";
    if (improvement > -10) return "#d97706";
    return "#b91c1c";
  }
  if (score >= 80) return "#0f766e";
  if (score >= 65) return "#4d9f75";
  if (score >= 50) return "#c0a33c";
  if (score >= 35) return "#d97706";
  return "#b91c1c";
}

function regionStyle(feature: Feature<RegionProperties>): L.PathOptions {
  const score = scoreValue(feature.properties, activeScore);
  const isSelected = selectedRegionId === regionId(feature.properties);
  return {
    color: isSelected ? "#74f0d2" : "#213244",
    weight: isSelected ? 3 : 1,
    fillColor: scoreColor(score, activeScore),
    fillOpacity: 0.6
  };
}

function boundsFromBox(box: [number, number, number, number]): L.LatLngBoundsExpression {
  const [west, south, east, north] = box;
  return [
    [south, west],
    [north, east]
  ];
}

function fitFeature(feature: Feature<RegionProperties>): void {
  const box = feature.properties.box;
  if (box) {
    map.fitBounds(boundsFromBox(box), { padding: [24, 24], maxZoom: 9 });
    return;
  }
  map.fitBounds(L.geoJSON(feature).getBounds(), { padding: [24, 24], maxZoom: 9 });
}

function sortedRegions(metric: ScoreMetric): Feature<RegionProperties>[] {
  return [...regionFeatures].sort((a, b) => {
    const aScore = scoreValue(a.properties, metric);
    const bScore = scoreValue(b.properties, metric);
    return scenarioViewMode === "change"
      ? Math.abs(bScore) - Math.abs(aScore)
      : bScore - aScore;
  });
}

function renderDatasets(): void {
  const list = document.getElementById("dataset-list");
  if (!list) return;
  list.innerHTML = "";

  datasets.forEach((dataset, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `dataset-button${index === 0 ? " active" : ""}`;
    button.innerHTML = `<strong>${escapeHtml(dataset.title)}</strong><span>${escapeHtml(dataset.type)}</span>`;
    button.addEventListener("click", () => {
      document.querySelectorAll(".dataset-button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      renderMetadata(dataset.id);
    });
    list.appendChild(button);
  });

  renderMetadata(datasets[0].id);
}

function renderMetadata(datasetId: string): void {
  const dataset = datasets.find((item) => item.id === datasetId);
  const content = document.getElementById("metadata-content");
  if (!dataset || !content) return;

  content.innerHTML = `
    <div class="metadata-row"><span>Title</span><strong>${escapeHtml(dataset.title)}</strong></div>
    <div class="metadata-row"><span>Dataset type</span><strong>${escapeHtml(dataset.type)}</strong></div>
    <div class="metadata-row"><span>Prototype file</span><strong>${escapeHtml(dataset.file)}</strong></div>
    <div class="metadata-row"><span>Intended service</span><strong>${escapeHtml(dataset.service)}</strong></div>
    <div class="metadata-row"><span>Data note</span><strong>${escapeHtml(dataset.note)}</strong></div>
  `;
}

function renderKpis(): void {
  const readinessScores = regionFeatures.map((feature) => scoreValue(feature.properties, "evReadinessScore"));
  const averageReadiness = readinessScores.reduce((sum, score) => sum + score, 0) / Math.max(1, readinessScores.length);
  const bestReadiness = sortedRegions("evReadinessScore")[0]?.properties;
  const underserved = sortedRegions("chargerDeficitScore")[0]?.properties;
  const priority = sortedRegions("investmentPriorityScore")[0]?.properties;
  const stationTotal = scenarioViewMode === "baseline"
    ? officialStations.features.length
    : scenarioViewMode === "scenario"
      ? officialStations.features.length + proposedStations.features.length
      : proposedStations.features.length;

  setText("kpi-total-stations", new Intl.NumberFormat("en-US").format(stationTotal));
  setText("kpi-average-density", scenarioViewMode === "change" ? formatScore(averageReadiness) : `${averageReadiness.toFixed(0)} / 100`);
  setText("kpi-best-region", bestReadiness ? regionName(bestReadiness) : "-");
  setText("kpi-underserved-region", underserved ? regionName(underserved) : "-");
  setText("kpi-priority-region", priority ? regionName(priority) : "-");
}

function renderRegionDetail(feature: Feature<RegionProperties> | null): void {
  const detail = document.getElementById("region-detail");
  if (!detail) return;

  if (!feature) {
    detail.innerHTML =
      '<p class="empty-state">Select a NRW district to inspect EV readiness, charger deficit, infrastructure opportunity and investment priority.</p>';
    return;
  }

  const p = feature.properties;
  const investment = scoreValue(p, "investmentPriorityScore");
  const readiness = scoreValue(p, "evReadinessScore");
  const deficit = scoreValue(p, "chargerDeficitScore");
  const infrastructure = scoreValue(p, "infrastructureOpportunityScore");
  const rank = numericValue(p, ["priorityRank", "priority_rank"], sortedRegions("investmentPriorityScore").findIndex((item) => regionId(item.properties) === regionId(p)) + 1);
  const tier = textValue(p, ["priorityTier", "priority_tier"], "screening");
  const recommendation =
    investment >= 70
      ? "High screening priority. Charger supply is weak relative to other NRW districts, so this district should be checked first once demand and road layers are joined."
      : readiness >= 70
        ? "Strong EV readiness in the selected view. Use the baseline comparison before changing investment priority."
        : "Medium screening case. Compare the baseline and scenario values before making a planning decision.";

  const comparison = p.baseline_ev_readiness_score === undefined ? "" : `
    <div class="comparison-grid" aria-label="Baseline and scenario comparison">
      <div><span>Baseline readiness</span><strong>${formatScore(numericValue(p, ["baseline_ev_readiness_score"], 0), false)}</strong></div>
      <div><span>Scenario readiness</span><strong>${formatScore(numericValue(p, ["scenario_ev_readiness_score"], 0), false)}</strong></div>
      <div><span>Change</span><strong>${formatScore(numericValue(p, ["ev_readiness_score_delta"], 0), true)}</strong></div>
    </div>`;

  detail.innerHTML = `
    <div class="detail-title">
      <h3>${escapeHtml(regionName(p))}</h3>
      <span class="score-pill" style="background:${scoreColor(investment, "investmentPriorityScore")}">${formatScore(investment)}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-metric"><span>NUTS-3</span><strong>${escapeHtml(textValue(p, ["nuts_code"], "-"))}</strong></div>
      <div class="detail-metric"><span>Stations</span><strong>${formatNumber(numericValue(p, ["stationCount", "chargers_total"], 0))}</strong></div>
      <div class="detail-metric"><span>Charging points</span><strong>${formatNumber(numericValue(p, ["charging_points_total"], 0))}</strong></div>
      <div class="detail-metric"><span>Fast chargers</span><strong>${formatNumber(numericValue(p, ["fast_chargers", "fast_chargers_total"], 0))}</strong></div>
      <div class="detail-metric"><span>EV readiness</span><strong>${formatScore(readiness)}</strong></div>
      <div class="detail-metric"><span>Charger deficit</span><strong>${formatScore(deficit)}</strong></div>
      <div class="detail-metric"><span>Priority rank</span><strong>#${formatNumber(rank)}</strong></div>
      <div class="detail-metric"><span>Infrastructure opportunity</span><strong>${formatScore(infrastructure)}</strong></div>
    </div>
    ${comparison}
    <p class="recommendation">${escapeHtml(recommendation)} Current tier: ${escapeHtml(tier)}.</p>
  `;
}

function selectRegion(feature: Feature<RegionProperties>, layer?: L.Layer): void {
  selectedRegionId = regionId(feature.properties);
  if (selectedLayer) regionLayer.resetStyle(selectedLayer);

  selectedLayer = layer ?? null;
  if (!selectedLayer) {
    regionLayer.eachLayer((candidate) => {
      const candidateFeature = (candidate as L.Layer & { feature?: Feature<RegionProperties> }).feature;
      if (candidateFeature && regionId(candidateFeature.properties) === selectedRegionId) selectedLayer = candidate;
    });
  }

  if (selectedLayer) {
    regionLayer.resetStyle(selectedLayer);
    (selectedLayer as L.Path).setStyle(regionStyle(feature));
  }

  renderRegionDetail(feature);
}

function renderRanking(metric: ScoreMetric = activeRanking): void {
  activeRanking = metric;
  const list = document.getElementById("ranking-list");
  if (!list) return;

  document.querySelectorAll(".ranking-tab").forEach((button) => {
    button.classList.toggle("active", (button as HTMLElement).dataset.ranking === metric);
  });

  list.innerHTML = "";
  sortedRegions(metric)
    .slice(0, 10)
    .forEach((feature, index) => {
      const p = feature.properties;
      const score = scoreValue(p, metric);
      const width = Math.max(8, Math.min(100, Math.abs(score)));
      const item = document.createElement("li");
      item.className = "ranking-item";
      item.innerHTML = `
        <span class="ranking-rank">#${index + 1}</span>
        <span class="ranking-dot"></span>
        <span class="ranking-name" title="${escapeHtml(regionName(p))}">${escapeHtml(regionName(p))}</span>
        <span class="ranking-bar"><i style="width:${width}%"></i></span>
        <span class="score-pill" style="background:${scoreColor(score, metric)}">${formatScore(score)}</span>
      `;
      item.addEventListener("click", () => {
        selectRegion(feature);
        fitFeature(feature);
      });
      list.appendChild(item);
    });
}

async function fetchGeoJson<T>(path: string): Promise<FeatureCollection<T>> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Could not load ${path}`);
  return response.json();
}

async function loadGeoJsonWithFallback<T>(primaryPath: string, fallbackPath: string): Promise<{ data: FeatureCollection<T>; source: string }> {
  if (primaryPath === fallbackPath) {
    return { data: await fetchGeoJson<T>(primaryPath), source: `Local GeoJSON: ${fallbackPath}` };
  }

  try {
    return { data: await fetchGeoJson<T>(primaryPath), source: `GeoNode WFS: ${primaryPath}` };
  } catch (error) {
    console.warn(`Falling back from ${primaryPath} to ${fallbackPath}`, error);
    return { data: await fetchGeoJson<T>(fallbackPath), source: `Local GeoJSON fallback: ${fallbackPath}` };
  }
}

function renderAnalytics(): void {
  regionLayer.clearLayers();
  regionLayer.addData({ type: "FeatureCollection", features: regionFeatures } as FeatureCollection<RegionProperties>);
  renderKpis();
  renderRanking();
  if (selectedRegionId) {
    const selected = regionFeatures.find((feature) => regionId(feature.properties) === selectedRegionId) ?? null;
    selectedLayer = null;
    renderRegionDetail(selected);
  }
}

function applyScenarioView(mode: ScenarioViewMode): void {
  scenarioViewMode = mode;
  document.querySelectorAll<HTMLElement>("[data-scenario-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.scenarioMode === mode);
  });
  if (scenarioMetricFeatures.length) {
    regionFeatures = scenarioMetricFeatures.map((feature) => ({
      ...feature,
      properties: projectScenarioProperties(feature.properties, mode) as RegionProperties
    }));
  }
  renderAnalytics();
}

function proposedStationId(feature: Feature<StationProperties>): string {
  const propertyId = textValue(feature.properties, ["id"], "");
  if (propertyId) return propertyId;
  const featureId = String(feature.id ?? "");
  return featureId.includes(".") ? featureId.slice(featureId.lastIndexOf(".") + 1) : featureId;
}

function renderProposedStations(): void {
  proposedStationLayer.clearLayers();
  proposedStationLayer.addData(proposedStations);
  setText(
    "scenario-station-count",
    `${proposedStations.features.length} station${proposedStations.features.length === 1 ? "" : "s"}`
  );
  const list = document.getElementById("scenario-station-list");
  if (!list) return;
  list.innerHTML = "";
  if (!proposedStations.features.length) {
    list.innerHTML = '<p class="empty-state">No proposed stations yet. Select “Add proposed station”, then click inside NRW.</p>';
    return;
  }
  proposedStations.features.forEach((feature) => {
    const p = feature.properties;
    const row = document.createElement("div");
    row.className = "scenario-station-row";
    const summary = document.createElement("button");
    summary.type = "button";
    summary.className = "scenario-station-summary";
    summary.innerHTML = `<strong>${escapeHtml(textValue(p, ["name"], "Proposed station"))}</strong>`
      + `<span>${formatNumber(numericValue(p, ["charging_points"], 0))} points · `
      + `${formatNumber(numericValue(p, ["power_kw"], 0))} kW</span>`;
    summary.addEventListener("click", () => {
      const point = feature.geometry as GeoJSON.Point;
      if (point?.type === "Point") map.setView([point.coordinates[1], point.coordinates[0]], 13);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "scenario-row-delete";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeScenarioStation(proposedStationId(feature)));
    row.append(summary, remove);
    list.appendChild(row);
  });
}

async function refreshScenarioData(successMessage?: string): Promise<void> {
  const [proposed, metrics] = await Promise.all([
    scenarioClient.loadProposedChargers(),
    scenarioClient.loadScenarioMetrics()
  ]);
  proposedStations = proposed as FeatureCollection<StationProperties>;
  scenarioMetricFeatures = metrics.features as Feature<RegionProperties>[];
  renderProposedStations();
  applyScenarioView(scenarioViewMode);
  setScenarioStatus(successMessage ?? "Scenario is ready. Indicators update after every edit.");
}

async function removeScenarioStation(id: string): Promise<void> {
  if (!id) return;
  setScenarioStatus("Removing proposed station…");
  try {
    await scenarioClient.remove(id);
    await refreshScenarioData("Station removed and district indicators recalculated.");
  } catch (error) {
    setScenarioStatus(error instanceof Error ? error.message : "Could not remove the station", true);
  }
}

function beginAddScenarioStation(): void {
  awaitingMapClick = true;
  map.getContainer().classList.add("scenario-pick-mode");
  setScenarioStatus("Click a location inside NRW for the proposed station.");
}

function closeScenarioDialog(): void {
  const modal = document.getElementById("scenario-modal") as HTMLElement | null;
  if (modal) modal.hidden = true;
  pendingLocation = null;
  awaitingMapClick = false;
  map.getContainer().classList.remove("scenario-pick-mode");
  setText("scenario-form-error", "");
}

function openScenarioDialog(location: L.LatLng): void {
  pendingLocation = location;
  setText("scenario-location", `${location.lat.toFixed(6)}, ${location.lng.toFixed(6)}`);
  const modal = document.getElementById("scenario-modal") as HTMLElement | null;
  if (modal) modal.hidden = false;
  (document.getElementById("scenario-name") as HTMLInputElement | null)?.focus();
}

map.on("click", (event) => {
  if (!awaitingMapClick) return;
  awaitingMapClick = false;
  map.getContainer().classList.remove("scenario-pick-mode");
  openScenarioDialog(event.latlng);
});

async function boot(): Promise<void> {
  renderDatasets();
  renderRegionDetail(null);

  const [regionsResult, stationsResult] = await Promise.all([
    loadGeoJsonWithFallback<RegionProperties>(
      geoserverWfsUrl(runtimeConfig.geonodeRegionsLayer),
      "data/nrw_regions_sample.geojson"
    ),
    loadGeoJsonWithFallback<StationProperties>(
      geoserverWfsUrl(runtimeConfig.geonodeStationsLayer),
      "data/nrw_charging_stations_sample.geojson"
    )
  ]);

  const regions = regionsResult.data;
  const stations = stationsResult.data;

  regionFeatures = regions.features as Feature<RegionProperties>[];
  officialStations = stations;
  stationLayer.addData(officialStations);
  renderAnalytics();
  renderProposedStations();
  const connectedSourceCount = [regionsResult.source, stationsResult.source]
    .filter((source) => source.startsWith("GeoNode")).length;
  setHeaderStatus(connectedSourceCount > 0 ? "GeoNode WFS connected" : "Local GeoJSON fallback");
  const note = document.querySelector(".map-note");
  if (note) {
    note.textContent =
      regionsResult.source.startsWith("GeoNode") || stationsResult.source.startsWith("GeoNode")
        ? "GeoNode-connected preview: regions and/or charging stations are being served from GeoServer WFS, with local GeoJSON fallback if the stack is offline."
        : "Local-first preview: GeoNode is offline, so the dashboard is using the checked-in NRW GeoJSON snapshot.";
  }
  map.fitBounds(regionLayer.getBounds().isValid() ? regionLayer.getBounds() : NRW_BOUNDS, { padding: [24, 24] });
  try {
    await refreshScenarioData();
  } catch (error) {
    console.warn("Scenario layers are not available", error);
    setScenarioStatus("Scenario editing is unavailable until the local GeoServer layers are initialized.", true);
  }
}

document.getElementById("score-mode")?.addEventListener("change", (event) => {
  activeScore = (event.target as HTMLSelectElement).value as ScoreMetric;
  regionLayer.setStyle((feature) => regionStyle(feature as Feature<RegionProperties>));
  renderRanking(activeScore);
  const selected = regionFeatures.find((feature) => regionId(feature.properties) === selectedRegionId) ?? null;
  if (selected) renderRegionDetail(selected);
});

document.querySelectorAll(".ranking-tab").forEach((button) => {
  button.addEventListener("click", () => renderRanking((button as HTMLElement).dataset.ranking as ScoreMetric));
});

document.querySelectorAll<HTMLElement>("[data-scenario-mode]").forEach((button) => {
  button.addEventListener("click", () => applyScenarioView(button.dataset.scenarioMode as ScenarioViewMode));
});

document.getElementById("official-layer-toggle")?.addEventListener("change", (event) => {
  if ((event.target as HTMLInputElement).checked) stationLayer.addTo(map);
  else stationLayer.removeFrom(map);
});

document.getElementById("scenario-layer-toggle")?.addEventListener("change", (event) => {
  if ((event.target as HTMLInputElement).checked) proposedStationLayer.addTo(map);
  else proposedStationLayer.removeFrom(map);
});

document.getElementById("scenario-add-button")?.addEventListener("click", beginAddScenarioStation);
document.getElementById("map-add-station")?.addEventListener("click", beginAddScenarioStation);
document.getElementById("scenario-modal-close")?.addEventListener("click", closeScenarioDialog);
document.getElementById("scenario-cancel")?.addEventListener("click", closeScenarioDialog);

document.getElementById("scenario-reset-button")?.addEventListener("click", async () => {
  if (!proposedStations.features.length) {
    setScenarioStatus("The scenario is already empty.");
    return;
  }
  if (!window.confirm("Remove all proposed charging stations and return to the baseline?")) return;
  setScenarioStatus("Resetting scenario…");
  try {
    await scenarioClient.reset();
    applyScenarioView("baseline");
    await refreshScenarioData("Scenario reset. Baseline indicators restored.");
  } catch (error) {
    setScenarioStatus(error instanceof Error ? error.message : "Could not reset the scenario", true);
  }
});

document.getElementById("scenario-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingLocation) return;
  const form = event.currentTarget as HTMLFormElement;
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  const name = (document.getElementById("scenario-name") as HTMLInputElement).value;
  const chargingPoints = Number((document.getElementById("scenario-points") as HTMLInputElement).value);
  const powerKw = Number((document.getElementById("scenario-power") as HTMLInputElement).value);
  if (submit) submit.disabled = true;
  setText("scenario-form-error", "");
  try {
    await scenarioClient.create({
      name,
      chargingPoints,
      powerKw,
      longitude: pendingLocation.lng,
      latitude: pendingLocation.lat
    });
    closeScenarioDialog();
    form.reset();
    applyScenarioView("scenario");
    await refreshScenarioData("Station added and district indicators recalculated.");
  } catch (error) {
    setText("scenario-form-error", error instanceof Error ? error.message : "Could not add the station");
  } finally {
    if (submit) submit.disabled = false;
  }
});

boot().catch((error) => {
  console.error(error);
  const note = document.querySelector(".map-note");
  if (note) {
    note.textContent =
      "NRW GeoJSON and GeoNode WFS could not be loaded. Check the local GeoNode stack or run `python3 -m http.server 8000 --directory frontend`.";
  }
});
