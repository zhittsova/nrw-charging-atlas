import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "../style.css";
import { createScenarioClient, type GeoJsonFeatureCollection } from "./scenarioClient";
import { ScenarioDialogState } from "./scenarioDialogState";
import { submitScenarioProposal } from "./scenarioSubmission";
import {
  CANONICAL_DISTRICT_FIELDS,
  CANONICAL_STATION_FIELDS,
  readLayer,
  type LayerRead,
  type LayerState
} from "./dataSources";
import {
  projectScenarioProperties,
  scenarioMetricCoverageIssue,
  stationTotalForView,
  type ScenarioViewMode
} from "./scenarioState";
import {
  AUTOBAHN_STYLE,
  basemapAvailabilityMessage,
  illuminatedScoreColor,
  REGIONAL_ROAD_STYLE,
  RENEWABLE_LEGEND_ITEMS,
  isDisplayedRenewableTechnology,
  officialStationStyle,
  operatorTooltip,
  renewableAssetStyle,
  renewableTechnologyLabel,
  selectAutobahnFeaturesForZoom,
  selectOfficialStationsForZoom,
  selectRenewableFeaturesForZoom,
  scoreColor as presentationScoreColor
} from "./mapPresentation";
import {
  detailContext,
  fastShareChange,
  fastShareReason,
  finiteNumber,
  formatCoveragePercentage,
  publishedPriorityRank,
  signedValue,
  sortDisplayRows,
  statewideFastShare,
  publishedScoreModelTerms,
  type FastShare,
  type NumericProperties
} from "./analyticsPresentation";

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
  chargers_total?: number;
  charging_points_total?: number;
  chargers_per_km2?: number;
  ev_readiness_score?: number;
  charger_deficit_score?: number;
  investment_priority_score?: number;
  infrastructure_opportunity_score?: number;
  priority_rank?: number;
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
  max_point_power_kw?: number;
  charging_points?: number;
  connectors?: number;
  status?: string;
  charger_type?: string;
  request_id?: string;
  [key: string]: unknown;
};

type RoadProperties = {
  source_id?: string;
  highway?: string;
  ref?: string;
  road_class?: string;
  road_number?: string | number;
  name?: string;
  traffic_total?: number;
  [key: string]: unknown;
};

type RenewableProperties = {
  source_id?: string;
  name?: string;
  operator?: string;
  asset_type?: string;
  technology?: string;
  capacity_mw?: number;
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
  scoreModelLayer: string;
  scenarioMetricsLayer: string;
  proposedChargersLayer: string;
  autobahnsLayer: string;
  regionalRoadsLayer: string;
  renewableAssetsLayer: string;
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
  geoserverBaseUrl: "",
  geonodeStationsLayer: "nrw:nrw_chargers",
  geonodeRegionsLayer: "nrw:nrw_ev_baseline_metrics",
  scoreModelLayer: "nrw:nrw_score_model",
  scenarioMetricsLayer: "nrw:nrw_ev_scenario_metrics",
  proposedChargersLayer: "nrw:proposed_chargers",
  autobahnsLayer: "nrw:nrw_autobahns",
  regionalRoadsLayer: "nrw:nrw_regional_roads",
  renewableAssetsLayer: "nrw:nrw_renewable_potential",
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

const scoreLabels: Record<ScoreMetric, string> = {
  investmentPriorityScore: "Investment Priority",
  evReadinessScore: "EV Readiness",
  chargerDeficitScore: "Charger Deficit",
  infrastructureOpportunityScore: "Infrastructure Opportunity"
};

const scoreKeys: Record<ScoreMetric, string[]> = {
  investmentPriorityScore: ["investment_priority_score"],
  evReadinessScore: ["ev_readiness_score"],
  chargerDeficitScore: ["charger_deficit_score"],
  infrastructureOpportunityScore: ["infrastructure_opportunity_score"]
};

let activeScore: ScoreMetric = "investmentPriorityScore";
let activeRanking: ScoreMetric = "investmentPriorityScore";
let scenarioViewMode: ScenarioViewMode = "baseline";
let regionFeatures: Feature<RegionProperties>[] = [];
let scenarioMetricFeatures: Feature<RegionProperties>[] = [];
let baselineRegionFeatures: Feature<RegionProperties>[] = [];
let officialStations: FeatureCollection<StationProperties> = { type: "FeatureCollection", features: [] };
let autobahnFeatures: FeatureCollection<RoadProperties> = { type: "FeatureCollection", features: [] };
let renewableAssets: FeatureCollection<RenewableProperties> = { type: "FeatureCollection", features: [] };
let proposedStations: FeatureCollection<StationProperties> = { type: "FeatureCollection", features: [] };
let selectedRegionId: string | null = null;
let selectedLayer: L.Layer | null = null;
let pendingLocation: L.LatLng | null = null;
let scenarioDialogTrigger: HTMLElement | null = null;
let scenarioKeyboardLocationEntry = false;
const scenarioDialogState = new ScenarioDialogState();
let awaitingMapClick = false;
let scenarioServiceAvailable = false;
type ScoreModelTerm = {
  measure?: unknown;
  measure_unit?: unknown;
  lower_bound?: unknown;
  upper_bound?: unknown;
  inverse_measure?: unknown;
};
let scoreModel: ScoreModelTerm[] = [];
const OWNED_REQUEST_IDS_KEY = "nrw-proposed-charger-request-ids";
const ownedRequestIds = new Set<string>((() => {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(OWNED_REQUEST_IDS_KEY) ?? "[]");
    return Array.isArray(stored) ? stored.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
})());
const layerStates: Partial<Record<"districts" | "stations" | "renewables" | "regional roads" | "autobahns", LayerRead<unknown>>> = {};

let illuminatedMap = true;
let basemapState: "available" | "fallback" = "available";
const map = L.map("map", { preferCanvas: true, zoomControl: true, scrollWheelZoom: false }).setView(NRW_CENTER, 7);
map.createPane("regions");
map.createPane("regionalRoads");
map.createPane("autobahnCasing");
map.createPane("autobahns");
map.createPane("renewableAssets");
map.createPane("stations");
map.createPane("proposedStations");
map.getPane("regions")!.style.zIndex = "400";
map.getPane("regionalRoads")!.style.zIndex = "430";
map.getPane("autobahnCasing")!.style.zIndex = "439";
map.getPane("autobahns")!.style.zIndex = "440";
map.getPane("renewableAssets")!.style.zIndex = "460";
map.getPane("stations")!.style.zIndex = "470";
map.getPane("proposedStations")!.style.zIndex = "490";

// A proposed-stations Canvas would occupy its whole upper pane and swallow
// clicks aimed at official stations beneath it. Keep the dense official layer
// on the map's Canvas renderer, and use SVG only for the smaller proposal
// layer: blank SVG space does not intercept pointer input, while proposal paths
// remain independently interactive.
const proposedStationRenderer = L.svg({ pane: "proposedStations" });

const vectorFallback = L.layerGroup();
const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

function activateBasemapFallback(): void {
  if (basemapState === "fallback") return;
  basemapState = "fallback";
  osm.removeFrom(map);
  vectorFallback.addTo(map);
  map.getContainer().classList.add("map-basemap-unavailable");
  renderReadStates();
}

vectorFallback.on("add", () => {
  basemapState = "fallback";
  map.getContainer().classList.add("map-basemap-unavailable");
  renderReadStates();
});
osm.on("tileerror", activateBasemapFallback);
osm.on("add", () => {
  basemapState = "available";
  map.getContainer().classList.remove("map-basemap-unavailable");
  renderReadStates();
});

const stationLayer = L.geoJSON(undefined, {
  pane: "stations",
  pointToLayer: (feature, latlng) => {
    const p = feature.properties as StationProperties;
    const maxPointPower = numericValue(p, ["max_point_power_kw"]);
    return L.circleMarker(latlng, officialStationStyle(maxPointPower, map.getZoom()));
  },
  onEachFeature: (feature, layer) => {
    const p = feature.properties as StationProperties;
    layer.bindTooltip(operatorTooltip(p.operator), {
      className: "operator-tooltip",
      direction: "top",
      offset: [0, -5],
      sticky: true
    });
    layer.bindPopup(`
      <div class="station-popup">
        <h3>${escapeHtml(textValue(p, ["name", "operator"], "Charging station"))}</h3>
        <p><strong>Operator:</strong> ${escapeHtml(textValue(p, ["operator"], "unknown"))}</p>
        <p><strong>District:</strong> ${escapeHtml(textValue(p, ["district_text", "district_name", "region"], "NRW"))}</p>
        <p><strong>Address:</strong> ${escapeHtml(textValue(p, ["street", "address"], "not available"))}</p>
        <p><strong>Power:</strong> ${formatNumber(numericValue(p, ["power_kw"]))} kW</p>
        <p><strong>Maximum point power:</strong> ${formatNumber(numericValue(p, ["max_point_power_kw"]))} kW</p>
        <p><strong>Charging points:</strong> ${formatNumber(numericValue(p, ["charging_points", "connectors"]))}</p>
        <p><strong>Type:</strong> ${escapeHtml(textValue(p, ["charger_type"], "not available"))}</p>
        <p><strong>Status:</strong> ${escapeHtml(textValue(p, ["status"], "not available"))}</p>
      </div>
    `);
  }
}).addTo(map);

function refreshOfficialStations(): void {
  const visibleStations = selectOfficialStationsForZoom(officialStations.features, map.getZoom());
  stationLayer.clearLayers();
  stationLayer.addData({
    type: "FeatureCollection",
    features: visibleStations
  } as FeatureCollection<StationProperties>);
}

const regionalRoadLayer = L.geoJSON(undefined, {
  pane: "regionalRoads",
  style: REGIONAL_ROAD_STYLE,
  onEachFeature: (feature, layer) => {
    const p = feature.properties as RoadProperties;
    const roadName = textValue(p, ["name", "road_number"], "Regional road");
    const roadClass = textValue(p, ["road_class"], "B/L");
    const traffic = numericValue(p, ["traffic_total"]);
    layer.bindTooltip(
      `<strong>${escapeHtml(roadName)}</strong><span>${escapeHtml(roadClass)} road${traffic !== null ? ` · ${formatNumber(traffic)} vehicles/day` : ""}</span>`,
      { className: "road-tooltip", sticky: true }
    );
  }
}).addTo(map);

const autobahnCasingLayer = L.geoJSON(undefined, {
  pane: "autobahnCasing",
  style: { color: "#fff1f2", weight: AUTOBAHN_STYLE.weight + 1.4, opacity: 0.68 }
});

const autobahnLineLayer = L.geoJSON(undefined, {
  pane: "autobahns",
  style: AUTOBAHN_STYLE,
  onEachFeature: (feature, layer) => {
    const p = feature.properties as RoadProperties;
    const label = textValue(p, ["ref", "name"], "Autobahn");
    layer.bindTooltip(`<strong>${escapeHtml(label)}</strong><span>Autobahn</span>`, {
      className: "road-tooltip autobahn-tooltip",
      sticky: true
    });
  }
});

const autobahnLayer = L.layerGroup([autobahnCasingLayer, autobahnLineLayer]).addTo(map);

const renewableAssetLayer = L.geoJSON(undefined, {
  pane: "renewableAssets",
  pointToLayer: (feature, latlng) => {
    const properties = feature.properties as RenewableProperties;
    return L.circleMarker(
      latlng,
      renewableAssetStyle(properties.technology, numericValue(properties, ["capacity_mw"]), map.getZoom())
    );
  },
  onEachFeature: (feature, layer) => {
    const properties = feature.properties as RenewableProperties;
    const technology = renewableTechnologyLabel(properties.technology);
    const capacity = numericValue(properties, ["capacity_mw"]);
    const operator = textValue(properties, ["operator"], "").trim();
    const operatorDetail = operator ? ` · ${escapeHtml(operator)}` : "";
    layer.bindTooltip(
      `<strong>${escapeHtml(technology)}</strong><span>${formatNumber(capacity)} MW${operatorDetail}</span>`,
      { className: "renewable-tooltip", direction: "top", sticky: true }
    );
    layer.bindPopup(`
      <div class="station-popup renewable-popup">
        <h3>${escapeHtml(technology)}</h3>
        <p><strong>Installed capacity:</strong> ${formatNumber(capacity)} MW</p>
        ${operator ? `<p><strong>Operator:</strong> ${escapeHtml(operator)}</p>` : ""}
        <p><strong>Status:</strong> ${escapeHtml(textValue(properties, ["status"], "not available"))}</p>
        <p><strong>Energy carrier:</strong> ${escapeHtml(textValue(properties, ["asset_type"], technology))}</p>
      </div>
    `);
  }
}).addTo(map);

function refreshAutobahns(): void {
  const visibleAutobahns = selectAutobahnFeaturesForZoom(autobahnFeatures.features, map.getZoom());
  const visibleCollection = {
    type: "FeatureCollection",
    features: visibleAutobahns
  } as FeatureCollection<RoadProperties>;
  autobahnCasingLayer.clearLayers();
  autobahnLineLayer.clearLayers();
  autobahnCasingLayer.addData(visibleCollection);
  autobahnLineLayer.addData(visibleCollection);
}

function refreshRenewableAssets(): void {
  const activeAssets = renewableAssets.features.filter((feature) => {
    const status = feature.properties?.status?.trim();
    return isDisplayedRenewableTechnology(feature.properties?.technology)
      && (!status || status === "In Betrieb");
  });
  const visibleAssets = selectRenewableFeaturesForZoom(activeAssets, map.getZoom());
  renewableAssetLayer.clearLayers();
  renewableAssetLayer.addData({
    type: "FeatureCollection",
    features: visibleAssets
  } as FeatureCollection<RenewableProperties>);
}

function refreshZoomLayers(): void {
  refreshOfficialStations();
  refreshAutobahns();
  refreshRenewableAssets();
  restoreOverlayOrder();
}

map.on("zoomend", refreshZoomLayers);

const proposedStationLayer = L.geoJSON(undefined, {
  pane: "proposedStations",
  pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
    pane: "proposedStations",
    renderer: proposedStationRenderer,
    radius: 6,
    dashArray: "3 2",
    color: "#fff1f2",
    weight: 1.5,
    fillColor: "#8b5cf6",
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
      <p><strong>Power:</strong> ${formatNumber(numericValue(p, ["power_kw"]))} kW</p>
      <p><strong>Charging points:</strong> ${formatNumber(numericValue(p, ["charging_points"]))}</p>
    `;
    const requestId = textValue(p, ["request_id"], "");
    if (requestId && ownedRequestIds.has(requestId)) {
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "popup-delete";
      removeButton.textContent = "Remove from scenario";
      removeButton.addEventListener("click", async () => {
        const id = textValue(p, ["id"], "");
        if (id) await removeScenarioStation(id, requestId);
      });
      popup.appendChild(removeButton);
    } else {
      const unavailable = document.createElement("p");
      unavailable.textContent = "Editing is unavailable because this browser did not create this saved proposal.";
      popup.appendChild(unavailable);
    }
    layer.bindPopup(popup);
  }
}).addTo(map);

const districtRenderer = L.svg({ pane: "regions", padding: 0.5 });
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
      OpenStreetMap: osm,
      "Vector-only fallback": vectorFallback
    },
    {
      "Proposed charging stations": proposedStationLayer,
      "Official charging stations": stationLayer,
      "Solar farms and wind energy": renewableAssetLayer,
      "Autobahns": autobahnLayer,
      "Federal and state roads": regionalRoadLayer,
      "NRW district indicators": regionLayer
    },
    { collapsed: true }
  )
  .addTo(map);

function renderOverlayLegend(): void {
  const row = (label: string, color: string, shape = "dot") =>
    `<span><i class="legend-${shape}" style="--symbol-color:${color}"></i>${escapeHtml(label)}</span>`;
  const items: string[] = [];
  if (map.hasLayer(stationLayer)) {
    items.push(row("Official station · point power <150 kW / unknown", officialStationStyle(null).fillColor));
    items.push(row("Official station · high power ≥150 kW", officialStationStyle(150).fillColor));
  }
  if (map.hasLayer(proposedStationLayer)) items.push(row("Proposed station", "#8b5cf6", "proposal"));
  if (map.hasLayer(renewableAssetLayer)) {
    items.push(...RENEWABLE_LEGEND_ITEMS.map(({ label, color }) => row(label, color)));
  }
  if (map.hasLayer(autobahnLayer)) items.push(row("Autobahns", AUTOBAHN_STYLE.color, "line"));
  if (map.hasLayer(regionalRoadLayer)) items.push(row("Federal and state roads", REGIONAL_ROAD_STYLE.color, "line"));
  const overlay = document.getElementById("overlay-legend");
  if (overlay) overlay.innerHTML = items.join("") + (map.hasLayer(stationLayer) || map.hasLayer(renewableAssetLayer)
    ? '<small>Zoom in for more sites. Renewable symbol size increases with installed MW.</small>' : "");
  const district = document.getElementById("district-legend");
  if (district) district.hidden = !map.hasLayer(regionLayer);
}
map.on("layeradd layerremove", renderOverlayLegend);
renderOverlayLegend();

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

function numericValue(properties: Record<string, unknown>, keys: string[]): number | null {
  return finiteNumber(firstValue(properties, keys));
}

function regionName(properties: RegionProperties): string {
  return textValue(properties, ["district_name", "name", "region_name"], "NRW district");
}

function regionId(properties: RegionProperties): string {
  return textValue(properties, ["nuts_code"], regionName(properties));
}

function scoreValue(properties: RegionProperties, metric: ScoreMetric): number | null {
  return numericValue(properties, scoreKeys[metric]);
}

function formatNumber(value: number | null): string {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function formatScore(value: number | null, signed = scenarioViewMode === "change"): string {
  if (value === null) return "—";
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return signed && value > 0 ? `+${formatted}` : formatted;
}

function formatMeasurement(value: number | null, unit = ""): string {
  return value === null ? "—" : `${formatNumber(value)}${unit ? ` ${unit}` : ""}`;
}

function detailMetric(label: string, value: string): string {
  return `<div class="detail-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function unavailableReason(properties: RegionProperties, metric: ScoreMetric): string {
  const key = scoreKeys[metric][0];
  return textValue(properties, [
    `${key}_unavailable_reason`,
    "energy_unavailable_reason",
    "charger_snapshot_unavailable_reason",
    "data_quality_flag"
  ], "No published reason is available.");
}

function scoreModelBounds(): string {
  if (!scoreModel.length) return "<p class=\"detail-note\">Published normalization bounds are unavailable in this data state.</p>";
  const rows = scoreModel.map((term) => {
    const measure = textValue(term as Record<string, unknown>, ["measure"], "Score input");
    const unit = textValue(term as Record<string, unknown>, ["measure_unit"], "");
    const lower = formatMeasurement(numericValue(term as Record<string, unknown>, ["lower_bound"]), unit);
    const upper = formatMeasurement(numericValue(term as Record<string, unknown>, ["upper_bound"]), unit);
    const inverse = term.inverse_measure === true ? " (inverse)" : "";
    return `<li><strong>${escapeHtml(measure)}${inverse}</strong>: ${escapeHtml(lower)} to ${escapeHtml(upper)}</li>`;
  });
  return `<ul class="detail-bounds">${rows.join("")}</ul>`;
}

function fastShareForCurrentView(): FastShare | ReturnType<typeof fastShareChange> {
  if (scenarioViewMode === "baseline") {
    return statewideFastShare(baselineRegionFeatures.map((feature) => feature.properties as NumericProperties));
  }
  if (scenarioViewMode === "scenario") {
    return statewideFastShare(scenarioMetricFeatures.map((feature) => feature.properties as NumericProperties), "scenario_");
  }
  return fastShareChange(scenarioMetricFeatures.map((feature) => feature.properties as NumericProperties));
}

function formatFastShareForView(value: ReturnType<typeof fastShareForCurrentView>): { value: string; note: string } {
  if ("percentagePoints" in value && value.available) {
    return { value: `${signedValue(value.percentagePoints)} pp`, note: "Scenario minus baseline; percentage points." };
  }
  if ("baseline" in value) {
    const reason = !value.baseline.available ? fastShareReason(value.baseline) : fastShareReason(value.scenario);
    return { value: "—", note: reason };
  }
  return value.available
    ? { value: `${value.percent.toFixed(1)}%`, note: `${formatNumber(value.fast)} fast of ${formatNumber(value.total)} classified stations.` }
    : { value: "—", note: fastShareReason(value) };
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
    srsName: "EPSG:4326",
    outputFormat: "application/json"
  });
  return `${baseUrl}/geoserver/ows?${query.toString()}`;
}

function scoreColor(score: number | null, metric: ScoreMetric = activeScore): string {
  return score === null ? "#475569" : (illuminatedMap ? illuminatedScoreColor : presentationScoreColor)(score, scenarioViewMode, metric);
}

function scoreTextColor(background: string): string {
  const linear = [1, 3, 5].map((start) => {
    const value = parseInt(background.slice(start, start + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  return luminance > 0.179 ? "#000000" : "#ffffff";
}

function regionStyle(feature: Feature<RegionProperties>): L.PathOptions {
  const score = scoreValue(feature.properties, activeScore);
  const isSelected = selectedRegionId === regionId(feature.properties);
  return {
    renderer: districtRenderer,
    className: "district-boundary",
    color: illuminatedMap ? (isSelected ? "#d2f8ff" : "#489dda") : (isSelected ? "#f8fafc" : "#253448"),
    weight: isSelected ? 3.2 : 0.9,
    fillColor: scoreColor(score, activeScore),
    fillOpacity: illuminatedMap ? 0.93
      : scenarioViewMode === "change" || score === null ? 0.46
        : 0.15 + 0.33 * Math.max(0, Math.min(100, score)) / 100
  };
}

function restoreOverlayOrder(): void {
  regionLayer.bringToBack();
  regionalRoadLayer.bringToFront();
  autobahnCasingLayer.bringToFront();
  autobahnLineLayer.bringToFront();
  renewableAssetLayer.bringToFront();
  stationLayer.bringToFront();
  proposedStationLayer.bringToFront();
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
  return sortDisplayRows(
    regionFeatures
      .map((feature) => ({ feature, value: scoreValue(feature.properties, metric) }))
      .filter((entry): entry is { feature: Feature<RegionProperties>; value: number } => entry.value !== null)
      .map(({ feature, value }) => ({
        item: feature,
        value,
        nutsCode: textValue(feature.properties, ["nuts_code"], regionName(feature.properties))
      })),
    scenarioViewMode === "change"
  ).map(({ item }) => item);
}

function renderKpis(): void {
  const isChangeView = scenarioViewMode === "change";
  const readinessScores = regionFeatures
    .map((feature) => scoreValue(feature.properties, "evReadinessScore"))
    .filter((score): score is number => score !== null);
  const averageReadiness = readinessScores.length
    ? readinessScores.reduce((sum, score) => sum + score, 0) / readinessScores.length
    : null;
  const bestReadiness = sortedRegions("evReadinessScore")[0]?.properties;
  const underserved = sortedRegions("chargerDeficitScore")[0]?.properties;
  const priority = sortedRegions("investmentPriorityScore")[0]?.properties;
  const stationDataUnavailable = layerStates.stations?.state === "unavailable";
  const stationTotal = stationTotalForView(
    officialStations.features.length,
    proposedStations.features.length,
    scenarioViewMode,
    !stationDataUnavailable
  );

  setText("kpi-total-stations-label", stationDataUnavailable
    ? "Total NRW stations (unavailable)"
    : isChangeView ? "Proposed stations" : "Total NRW stations");
  setText("kpi-average-readiness-label", isChangeView ? "Avg EV readiness change" : "Avg EV readiness");
  setText("kpi-best-readiness-label", isChangeView ? "Highest readiness change" : "Best EV readiness");
  setText("kpi-underserved-label", isChangeView ? "Highest charging-gap change" : "Most underserved");
  setText("kpi-priority-label", isChangeView ? "Highest priority change" : "Highest priority");
  setText("kpi-fast-share-label", isChangeView ? "Fast-share change" : "Fast charging share");
  setText(
    "kpi-underserved-note",
    isChangeView ? "Highest signed change; increases, then unchanged values, then decreases." : "Largest charging gap: 100 − EV Readiness."
  );
  setText(
    "kpi-priority-note",
    isChangeView
      ? "Highest signed change; increases, then unchanged values, then decreases."
      : "60% charging gap + 40% infrastructure opportunity."
  );
  setText("kpi-total-stations", stationTotal === null ? "—" : new Intl.NumberFormat("en-US").format(stationTotal));
  setText("kpi-total-stations-note", stationDataUnavailable
    ? "Official station data could not be read, so a total cannot be calculated."
    : "");
  setText("kpi-average-density", averageReadiness === null ? "—" : isChangeView ? formatScore(averageReadiness) : `${averageReadiness.toFixed(0)} / 100`);
  setText("kpi-best-region", bestReadiness ? regionName(bestReadiness) : "-");
  setText("kpi-underserved-region", underserved ? regionName(underserved) : "-");
  setText("kpi-priority-region", priority ? regionName(priority) : "-");
  const fastShare = formatFastShareForView(fastShareForCurrentView());
  setText("kpi-fast-share", fastShare.value);
  setText("kpi-fast-share-note", fastShare.note);
}

function renderRegionDetail(feature: Feature<RegionProperties> | null): void {
  const detail = document.getElementById("region-detail");
  if (!detail) return;

  if (!feature) {
    detail.innerHTML =
      '<p class="empty-state">Select a district on the map or in the ranking to see its charging supply, the screening rationale and what to check before choosing a site.</p>';
    return;
  }

  const p = feature.properties;
  const usesBaselineContext = scenarioViewMode !== "baseline";
  const baselineContext = usesBaselineContext
    ? baselineRegionFeatures.find((candidate) => regionId(candidate.properties) === regionId(p))?.properties
    : undefined;
  // Scenario metrics intentionally publish only the context score needed for
  // priority calculation. Detail fields come from the canonical baseline row:
  // proposed chargers do not alter transport, grid, renewable or energy input.
  const contextProperties = detailContext(p, baselineContext, usesBaselineContext);
  const investment = scoreValue(p, "investmentPriorityScore");
  const readiness = scoreValue(p, "evReadinessScore");
  const deficit = scoreValue(p, "chargerDeficitScore");
  const infrastructure = scoreValue(contextProperties, "infrastructureOpportunityScore");
  const isChangeView = scenarioViewMode === "change";
  const priorityForRank = isChangeView
    ? numericValue(p, ["scenario_investment_priority_score"])
    : investment;
  const rank = publishedPriorityRank(priorityForRank, numericValue(p, ["priority_rank"]));
  const operatingAssetCapacity = numericValue(contextProperties, ["operating_asset_renewable_capacity_mw"]);
  const operatingAssetNote = operatingAssetCapacity === 0
    ? '<p class="detail-note">Zero operating assets recorded. Where a normalized renewable component is 50, the published equal-bound rule applies.</p>'
    : "";
  const recommendation = isChangeView
    ? "This is a change view, not a site recommendation. Positive EV readiness means a stronger relative charging score; negative deficit or priority means less relative charging need."
    : investment === null
      ? "Priority is unavailable: check data coverage before comparing this district."
      : `Screening priority ${formatScore(investment)} / 100 combines a charging-gap score of ${formatScore(deficit)} and infrastructure context of ${formatScore(infrastructure)}. Use it to choose where to investigate, then validate an individual site.`;
  const labels = isChangeView
    ? {
        stations: "Station change",
        points: "Charging-point change",
        fastChargers: "Fast-charger change",
        readiness: "EV readiness change",
        deficit: "Charger-deficit change",
        rank: "Scenario priority rank",
        infrastructure: "Infrastructure context (unchanged)"
      }
    : {
        stations: "Stations",
        points: "Charging points",
        fastChargers: "Fast chargers",
        readiness: "EV readiness",
        deficit: "Charger deficit",
        rank: "Priority rank",
        infrastructure: usesBaselineContext ? "Infrastructure context (unchanged)" : "Infrastructure opportunity"
      };

  const comparison = p.baseline_ev_readiness_score === undefined ? "" : `
    <div class="comparison-grid" aria-label="Baseline and scenario comparison">
      <div><span>Baseline readiness</span><strong>${formatScore(numericValue(p, ["baseline_ev_readiness_score"]), false)}</strong></div>
      <div><span>Scenario readiness</span><strong>${formatScore(numericValue(p, ["scenario_ev_readiness_score"]), false)}</strong></div>
      <div><span>Change</span><strong>${formatScore(numericValue(p, ["ev_readiness_score_delta"]), true)}</strong></div>
    </div>`;

  detail.innerHTML = `
    <div class="detail-title">
      <h3>${escapeHtml(regionName(p))}</h3>
      <span class="score-pill" style="background:${scoreColor(investment, "investmentPriorityScore")}">${formatScore(investment)}</span>
    </div>
    ${!isChangeView ? `<div class="site-evidence">
      <h4>Charging supply in context</h4>
      <p><strong>${formatNumber(numericValue(p, ["charging_points_per_100k_population"]))}</strong> charging points per 100,000 residents</p>
      <p><strong>${formatNumber(operatingAssetCapacity)} MW</strong> of existing renewable capacity in the district</p>
      <small>— means unavailable. Existing renewable capacity does not establish potential for a new wind or solar site.</small>
    </div>` : ""}
    <div class="detail-grid">
      <div class="detail-metric"><span>NUTS-3</span><strong>${escapeHtml(textValue(p, ["nuts_code"], "-"))}</strong></div>
      <div class="detail-metric"><span>${labels.stations}</span><strong>${formatNumber(numericValue(p, ["chargers_total"]))}</strong></div>
      <div class="detail-metric"><span>${labels.points}</span><strong>${formatNumber(numericValue(p, ["charging_points_total"]))}</strong></div>
      <div class="detail-metric"><span>${labels.fastChargers}</span><strong>${formatNumber(numericValue(p, ["fast_chargers_total"]))}</strong></div>
      <div class="detail-metric"><span>${labels.readiness}</span><strong>${formatScore(readiness)}</strong></div>
      <div class="detail-metric"><span>${labels.deficit}</span><strong>${formatScore(deficit)}</strong></div>
      <div class="detail-metric"><span>${labels.rank}</span><strong>${rank === null ? "—" : `#${formatNumber(rank)}`}</strong></div>
      <div class="detail-metric"><span>${labels.infrastructure}</span><strong>${formatScore(infrastructure, false)}</strong></div>
    </div>
    ${comparison}
    <details class="detail-evidence" open>
      <summary>District evidence, quality and method context</summary>
      <p class="detail-note">${isChangeView
        ? "Transport, grid, renewable and energy context are baseline-only. They do not change when only a proposed charger changes; a known unchanged value is shown as context, while unknown remains unavailable."
        : "Scores are published by the canonical PostGIS model. Components below are not recalculated in the browser."}</p>
      <div class="detail-grid">
        ${detailMetric(isChangeView ? "Charging-point density component change" : "Charging-point density component", formatScore(numericValue(p, ["charger_density_score"]), isChangeView))}
        ${detailMetric(isChangeView ? "Charger accessibility component change" : "Charger accessibility component", formatScore(numericValue(p, ["charger_accessibility_score"]), isChangeView))}
        ${detailMetric(isChangeView ? "Population coverage component change" : "Population coverage component", formatScore(numericValue(p, ["population_adjusted_coverage_score"]), isChangeView))}
        ${detailMetric("Transport Load", formatScore(numericValue(contextProperties, ["transport_load_score"]), false))}
        ${detailMetric("Traffic intensity component", formatScore(numericValue(contextProperties, ["traffic_intensity_score"]), false))}
        ${detailMetric("Road-density component", formatScore(numericValue(contextProperties, ["traffic_road_density_score"]), false))}
        ${detailMetric("Road-proximity component", formatScore(numericValue(contextProperties, ["road_proximity_score"]), false))}
        ${detailMetric("Grid Readiness Proxy", formatScore(numericValue(contextProperties, ["grid_readiness_proxy_score"]), false))}
        ${detailMetric("Substation-proximity component", formatScore(numericValue(contextProperties, ["substation_proximity_score"]), false))}
        ${detailMetric("Voltage-line-density component", formatScore(numericValue(contextProperties, ["voltage_line_density_score"]), false))}
        ${detailMetric("Substation-density component", formatScore(numericValue(contextProperties, ["substation_density_score"]), false))}
        ${detailMetric("Renewable Context", formatScore(numericValue(contextProperties, ["renewable_context_score"]), false))}
        ${detailMetric("Renewable-capacity component", formatScore(numericValue(contextProperties, ["renewable_capacity_density_score"]), false))}
        ${detailMetric("Technology-diversity component", formatScore(numericValue(contextProperties, ["renewable_technology_diversity_score"]), false))}
        ${detailMetric("Local Energy Balance", formatScore(numericValue(contextProperties, ["local_energy_balance_score"]), false))}
        ${detailMetric("Renewable Growth", formatScore(numericValue(contextProperties, ["renewable_growth_score"]), false))}
        ${detailMetric("Grid Absorption Risk Proxy", formatScore(numericValue(contextProperties, ["grid_absorption_risk_proxy_score"]), false))}
      </div>
      <h4>Transport measurements</h4>
      <div class="detail-grid">
        ${detailMetric("Traffic intensity", formatMeasurement(numericValue(contextProperties, ["traffic_intensity_dtv"]), "vehicles/day"))}
        ${detailMetric("Traffic-weighted road density", formatMeasurement(numericValue(contextProperties, ["traffic_weighted_road_density"]), "vehicle-km/day/km²"))}
        ${detailMetric("Road length", formatMeasurement(numericValue(contextProperties, ["traffic_road_length_km"]), "km"))}
        ${detailMetric("Road length with traffic", formatMeasurement(numericValue(contextProperties, ["traffic_measured_length_km"]), "km"))}
        ${detailMetric("Traffic length coverage", formatCoveragePercentage(contextProperties["traffic_length_coverage"]))}
        ${detailMetric("Distance to nearest road", formatMeasurement(numericValue(contextProperties, ["distance_to_nearest_road_m"]), "m"))}
      </div>
      <h4>Grid-proxy measurements</h4>
      <div class="detail-grid">
        ${detailMetric("Voltage-weighted line density", formatMeasurement(numericValue(contextProperties, ["voltage_weighted_line_density"]), "kV-km/km²"))}
        ${detailMetric("Substation density", formatMeasurement(numericValue(contextProperties, ["substation_density"]), "substations/km²"))}
        ${detailMetric("Distance to nearest substation", formatMeasurement(numericValue(contextProperties, ["distance_to_nearest_substation_m"]), "m"))}
        ${detailMetric("Mapped grid-line length", formatMeasurement(numericValue(contextProperties, ["grid_line_length_km"]), "km"))}
        ${detailMetric("Mapped substations", formatNumber(numericValue(contextProperties, ["substation_count"])))}
        ${detailMetric("Maximum mapped voltage", formatMeasurement(numericValue(contextProperties, ["maximum_mapped_voltage_kv"]), "kV"))}
        ${detailMetric("Line-voltage coverage", formatCoveragePercentage(contextProperties["line_voltage_coverage"]))}
        ${detailMetric("Substation-voltage coverage", formatCoveragePercentage(contextProperties["substation_voltage_coverage"]))}
      </div>
      <h4>Renewable operating assets</h4>
      <div class="detail-grid">
        ${detailMetric("Operating-asset renewable capacity", formatMeasurement(operatingAssetCapacity, "MW"))}
        ${detailMetric("Operating-asset capacity density", formatMeasurement(numericValue(contextProperties, ["operating_asset_renewable_capacity_mw_per_km2"]), "MW/km²"))}
        ${detailMetric("Renewable installations", formatNumber(numericValue(contextProperties, ["renewable_installation_count"])))}
        ${detailMetric("Operating renewable technologies", formatNumber(numericValue(contextProperties, ["renewable_technology_count"])))}
      </div>
      ${operatingAssetNote}
      <h4>Energy balance and growth</h4>
      <div class="detail-grid">
        ${detailMetric("Electricity consumption", formatMeasurement(numericValue(contextProperties, ["consumption_mwh"]), "MWh"))}
        ${detailMetric("Published renewable generation", formatMeasurement(numericValue(contextProperties, ["published_generation_mwh"]), "MWh"))}
        ${detailMetric("Estimated wind generation", formatMeasurement(numericValue(contextProperties, ["estimated_wind_generation_mwh"]), "MWh"))}
        ${detailMetric("Total renewable generation", formatMeasurement(numericValue(contextProperties, ["total_renewable_generation_mwh"]), "MWh"))}
        ${detailMetric("Renewable balance ratio", formatNumber(numericValue(contextProperties, ["renewable_balance_ratio"])))}
        ${detailMetric("Renewable coverage", formatMeasurement(numericValue(contextProperties, ["renewable_coverage_pct"]), "%"))}
        ${detailMetric("Renewable net addition (3 years)", formatMeasurement(numericValue(contextProperties, ["renewable_net_addition_3y_mw"]), "MW"))}
        ${detailMetric("Renewable growth density", formatMeasurement(numericValue(contextProperties, ["renewable_growth_density_mw_per_km2"]), "MW/km²"))}
        ${detailMetric("Expected municipalities", formatNumber(numericValue(contextProperties, ["expected_municipalities"])))}
        ${detailMetric("Growth years reported / required", `${formatNumber(numericValue(contextProperties, ["growth_years_reported"]))} / ${formatNumber(numericValue(contextProperties, ["growth_years_required"]))}`)}
        ${detailMetric("Wind full-load hours", formatMeasurement(numericValue(contextProperties, ["wind_full_load_hours"]), "hours/year"))}
        ${detailMetric("Renewable growth window", formatMeasurement(numericValue(contextProperties, ["renewable_growth_window_years"]), "years"))}
        ${detailMetric("Wind estimate note", textValue(contextProperties, ["wind_estimate_note"], "—"))}
      </div>
      <h4>Municipal workbook capacity</h4>
      <div class="detail-grid">
        ${detailMetric("Workbook renewable capacity", formatMeasurement(numericValue(contextProperties, ["municipal_workbook_renewable_capacity_mw"]), "MW"))}
        ${detailMetric("Workbook capacity reporting year", textValue(contextProperties, ["municipal_workbook_capacity_reporting_year"], "—"))}
        ${detailMetric("Workbook capacity coverage", formatCoveragePercentage(contextProperties["municipal_workbook_renewable_capacity_coverage"]))}
        ${detailMetric("Workbook capacity unavailable reason", textValue(contextProperties, ["municipal_workbook_renewable_capacity_unavailable_reason"], "—"))}
      </div>
      <h4>Data quality and coverage</h4>
      <div class="detail-grid">
        ${detailMetric("Overall score quality", textValue(contextProperties, ["data_quality_flag"]))}
        ${detailMetric("Transport quality", textValue(contextProperties, ["transport_data_quality_flag"]))}
        ${detailMetric("Grid quality", textValue(contextProperties, ["grid_data_quality_flag"]))}
        ${detailMetric("Energy quality", textValue(contextProperties, ["energy_data_quality_flag"]))}
        ${detailMetric("Consumption municipality coverage", formatCoveragePercentage(contextProperties["consumption_municipal_coverage"]))}
        ${detailMetric("Renewable municipality coverage", formatCoveragePercentage(contextProperties["renewable_municipal_coverage"]))}
        ${detailMetric("Municipal workbook capacity coverage", formatCoveragePercentage(contextProperties["municipal_workbook_renewable_capacity_coverage"]))}
        ${detailMetric("Energy unavailable reason", textValue(contextProperties, ["energy_unavailable_reason"], "—"))}
      </div>
      <h4>Source years and published method</h4>
      <div class="detail-grid">
        ${detailMetric("Population source year", textValue(contextProperties, ["population_source_year"]))}
        ${detailMetric("Energy reporting year", textValue(contextProperties, ["energy_reporting_year"]))}
        ${detailMetric("Charger snapshot date", textValue(contextProperties, ["charger_snapshot_date"], "—"))}
        ${detailMetric("Charger snapshot source", textValue(contextProperties, ["charger_snapshot_source"], "—"))}
        ${detailMetric("Snapshot-date availability", textValue(contextProperties, ["charger_snapshot_unavailable_reason"], "recorded"))}
        ${detailMetric("Formula version", textValue(contextProperties, ["formula_version"]))}
        ${detailMetric("Formula version date", textValue(contextProperties, ["formula_version_date"]))}
      </div>
      <h4>Published 5th–95th normalization bounds</h4>
      ${scoreModelBounds()}
    </details>
    ${investment === null ? `<p class="detail-note">Unavailable reason: ${escapeHtml(unavailableReason(p, "investmentPriorityScore"))}</p>` : ""}
    <p class="recommendation">${escapeHtml(recommendation)}</p>
    <details class="site-next-step"><summary>What to check next</summary>
      <p><strong>Charging:</strong> inspect nearby stations and roads, confirm local demand and land access, then request grid connection information. Add a proposal to compare charging supply before and after.</p>
      <p><strong>New wind or solar:</strong> this map shows existing installations. Site screening also needs wind or solar resource, land constraints, permits and confirmed connection capacity.</p>
    </details>
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
  regionLayer.eachLayer((layer) => {
    const district = layer as L.Path & { feature?: Feature<RegionProperties> };
    const priority = district.feature ? scoreValue(district.feature.properties, "investmentPriorityScore") : null;
    district.getElement()?.classList.toggle("district-priority-warm",
      illuminatedMap && activeScore === "investmentPriorityScore" && scenarioViewMode !== "change"
      && priority !== null && priority >= 65);
  });
  const ranked = sortedRegions(metric);
  const unavailable = regionFeatures.filter((feature) => scoreValue(feature.properties, metric) === null);
  setText("ranking-count", `${ranked.length} ranked${unavailable.length ? `; ${unavailable.length} unavailable` : ""}`);
  setText("ranking-explanation", scenarioViewMode === "change"
    ? "Signed changes descend: increases, then unchanged values, then decreases. These are score-point deltas, not current scores; Investment Priority retains its scenario SQL rank as context, never a delta rank."
    : metric === "investmentPriorityScore"
      ? "Available districts use the published SQL priority rank. Equal scores retain the same rank; unavailable districts are listed separately."
      : "Available districts are ordered by score only. Investment-priority rank is not reused for another indicator; unavailable districts are listed separately.");
  const legend = document.getElementById("district-legend");
  if (legend) {
    const bins: [number, string][] = scenarioViewMode === "change"
      ? [[-12, "−10 or less"], [-5, "−10 to 0"], [0, "No change"], [5, "0 to +10"], [12, "+10 or more"]]
      : [[10, "<35"], [40, "35–<50"], [55, "50–<65"], [70, "65–<80"], [90, "80–100"]];
    const continuous = scenarioViewMode !== "change";
    const gradient = Array.from({ length: 21 }, (_, index) => {
      const value = index * 5;
      return `${scoreColor(value, activeScore)} ${value}%`;
    }).join(",");
    const scale = continuous
      ? `<div class="score-ramp" role="img" aria-label="Score 0 to 100: ${illuminatedMap ? "deep plum for low scores, neon mint for high scores" : "light blue for low scores, deep purple for high scores"}" style="background:linear-gradient(90deg,${gradient})"></div>
         <div class="score-ticks"><span>0 · Low</span><span>25</span><span>50</span><span>75</span><span>100 · High</span></div>`
      : `<div class="score-bins">${bins.map(([value, label]) => `<span><i style="background:${scoreColor(value, activeScore)}"></i>${label}</span>`).join("")}</div>`;
    legend.innerHTML = `<div class="score-key-heading"><strong>${escapeHtml(scoreLabels[activeScore])}</strong><span>${scenarioViewMode === "change" ? "Change in points" : "Score / 100"}</span></div>`
      + scale + `<div class="score-key-footer"><span><i style="background:${scoreColor(null, activeScore)}"></i>No data</span><span>${activeScore === "investmentPriorityScore" && scenarioViewMode !== "change" ? "Higher score = higher district screening priority" : "Colours represent the selected indicator"}</span></div>`;

  }
  const list = document.getElementById("ranking-list");
  if (!list) return;

  document.querySelectorAll(".ranking-tab").forEach((button) => {
    button.classList.toggle("active", (button as HTMLElement).dataset.ranking === metric);
    button.setAttribute("aria-pressed", String((button as HTMLElement).dataset.ranking === metric));
  });

  list.innerHTML = "";
  ranked
    .forEach((feature) => {
      const p = feature.properties;
      const score = scoreValue(p, metric);
      const width = Math.max(8, Math.min(100, Math.abs(score!)));
      const item = document.createElement("li");
      item.className = "ranking-item";
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      const rank = metric === "investmentPriorityScore"
        ? publishedPriorityRank(
          scenarioViewMode === "change"
            ? numericValue(p, ["scenario_investment_priority_score"])
            : score,
          numericValue(p, ["priority_rank"])
        )
        : null;
      const lead = rank === null ? (scenarioViewMode === "change" ? "Δ" : "Score") : `#${formatNumber(rank)}`;
      const direction = scenarioViewMode === "change"
        ? (score! > 0 ? "increase" : score! < 0 ? "decrease" : "unchanged")
        : "";
      item.setAttribute("aria-label", `${regionName(p)}, ${scoreLabels[metric]} ${formatScore(score)}${direction ? `, ${direction}` : ""}. Show district.`);
      item.innerHTML = `
        <span class="ranking-rank">${escapeHtml(lead)}</span>
        <span class="ranking-dot"></span>
        <span class="ranking-name" title="${escapeHtml(regionName(p))}">${escapeHtml(regionName(p))}</span>
        <span class="ranking-bar"><i style="width:${width}%"></i></span>
        <span class="score-pill ranking-score" style="--score-color:${scoreColor(score, metric)};--score-ink:${scoreTextColor(scoreColor(score, metric))}">${formatScore(score)}${direction ? ` ${direction}` : ""}</span>
      `;
      item.addEventListener("click", () => {
        selectRegion(feature);
        fitFeature(feature);
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); }
      });
      list.appendChild(item);
    });
  if (unavailable.length) {
    const heading = document.createElement("li");
    heading.className = "ranking-unavailable";
    heading.textContent = `Unavailable ${scoreLabels[metric]} (${unavailable.length}) — not ranked`;
    list.appendChild(heading);
    unavailable.forEach((feature) => {
      const item = document.createElement("li");
      item.className = "ranking-item ranking-item-unavailable";
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.textContent = `${regionName(feature.properties)} — unavailable: ${unavailableReason(feature.properties, metric)}`;
      item.addEventListener("click", () => {
        selectRegion(feature);
        fitFeature(feature);
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); }
      });
      list.appendChild(item);
    });
  }
}

function readStateLabel(state: LayerState): string {
  return state === "live" ? "live WFS" : state === "snapshot" ? "canonical snapshot" : state === "stale" ? "stale snapshot" : "unavailable";
}

function renderReadStates(): void {
  const states = Object.entries(layerStates)
    .map(([layer, result]) => `${layer}: ${readStateLabel(result.state)}`);
  const required = [layerStates.districts, layerStates.stations];
  const allLive = required.every((result) => result?.state === "live");
  setHeaderStatus(allLive ? "Live WFS data" : states.some((state) => state.includes("unavailable")) ? "Data availability limited" : "Canonical snapshot data");
  const districtState = layerStates.districts;
  setText("data-quality-note", districtState?.state === "live"
    ? "District data is live from the canonical WFS layer. Scores are relative screening measures; inspect the methods and district details before drawing conclusions."
    : districtState?.state === "snapshot" || districtState?.state === "stale"
      ? `${districtState.state === "stale" ? "Stale" : "Canonical"} district snapshot in use. It carries the same published fields as the live layer; source state is shown below.`
      : "District comparisons are unavailable because neither the canonical WFS layer nor a valid canonical snapshot could be read.");
  const availability = document.getElementById("map-availability-note");
  if (availability) {
    const unavailable = Object.entries(layerStates)
      .filter(([, result]) => result.state === "unavailable")
      .map(([layer]) => layer);
    const basemapNote = basemapAvailabilityMessage(basemapState);
    availability.textContent = unavailable.length
      ? `${basemapNote} Unavailable map layers: ${unavailable.join(", ")}. The remaining layers are still usable. `
      : `${basemapNote} ${states.join("; ") || "data unavailable"}. Renewable overlay: operating wind assets and ground-mounted solar farms only; building-mounted solar is hidden. `;
  }
}

function setScenarioAvailability(available: boolean, detail: string): void {
  scenarioServiceAvailable = available;
  document.querySelectorAll<HTMLButtonElement>("[data-scenario-mode], #scenario-add-button, #map-add-station, #scenario-reset-button")
    .forEach((control) => { control.disabled = !available && control.dataset.scenarioMode !== "baseline"; });
  if (!available) {
    scenarioViewMode = "baseline";
    regionFeatures = baselineRegionFeatures;
    document.querySelectorAll<HTMLElement>("[data-scenario-mode]").forEach((button) => {
      button.classList.toggle("active", button.dataset.scenarioMode === "baseline");
    });
    renderAnalytics();
  }
  setScenarioStatus(available ? detail : `Scenario editing is unavailable: ${detail}. Baseline data remains read-only.`, !available);
}

function renderAnalytics(): void {
  regionLayer.clearLayers();
  regionLayer.addData({ type: "FeatureCollection", features: regionFeatures } as FeatureCollection<RegionProperties>);
  restoreOverlayOrder();
  renderKpis();
  renderQuestionExplanation();
  renderRanking();
  if (selectedRegionId) {
    const selected = regionFeatures.find((feature) => regionId(feature.properties) === selectedRegionId) ?? null;
    selectedLayer = null;
    renderRegionDetail(selected);
  }
}

function applyScenarioView(mode: ScenarioViewMode): void {
  if (mode !== "baseline" && !scenarioServiceAvailable) {
    setScenarioAvailability(false, "the scenario service is not available");
    return;
  }
  scenarioViewMode = mode;
  document.querySelectorAll<HTMLElement>("[data-scenario-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.scenarioMode === mode);
  });
  if (mode === "baseline") {
    regionFeatures = baselineRegionFeatures;
  } else {
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

function proposalRequestId(feature: Feature<StationProperties>): string | null {
  const requestId = textValue(feature.properties, ["request_id"], "");
  return requestId || null;
}

function persistOwnedRequestIds(): void {
  try {
    window.sessionStorage.setItem(OWNED_REQUEST_IDS_KEY, JSON.stringify([...ownedRequestIds]));
  } catch {
    // Browser storage is only a convenience for limiting reset/delete scope.
    // A storage failure must never broaden a write to other proposals.
  }
}

function rememberOwnedRequestId(requestId: string): void {
  ownedRequestIds.add(requestId);
  persistOwnedRequestIds();
}

function forgetOwnedRequestId(requestId: string | null): void {
  if (!requestId) return;
  ownedRequestIds.delete(requestId);
  persistOwnedRequestIds();
}

function requestIdForNewProposal(): string {
  if (!window.crypto?.randomUUID) throw new Error("This browser cannot create a safe proposal request identifier");
  return window.crypto.randomUUID();
}

function renderProposedStations(): void {
  proposedStationLayer.clearLayers();
  proposedStationLayer.addData(proposedStations);
  restoreOverlayOrder();
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
      + `<span>${formatNumber(numericValue(p, ["charging_points"]))} points · `
      + `${formatNumber(numericValue(p, ["power_kw"]))} kW total · `
      + `${formatNumber(numericValue(p, ["max_point_power_kw"]))} kW max point</span>`;
    summary.addEventListener("click", () => {
      const point = feature.geometry as GeoJSON.Point;
      if (point?.type === "Point") map.setView([point.coordinates[1], point.coordinates[0]], 13);
    });
    const requestId = proposalRequestId(feature);
    if (requestId && ownedRequestIds.has(requestId)) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "scenario-row-delete";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeScenarioStation(proposedStationId(feature), requestId));
      row.append(summary, remove);
    } else {
      const unavailable = document.createElement("span");
      unavailable.className = "scenario-row-unavailable";
      unavailable.textContent = "Saved proposal — editing unavailable in this browser";
      row.append(summary, unavailable);
    }
    list.appendChild(row);
  });
}

async function refreshScenarioData(successMessage?: string): Promise<void> {
  const [proposed, metrics] = await Promise.all([
    scenarioClient.loadProposedChargers(),
    scenarioClient.loadScenarioMetrics()
  ]);
  const issue = scenarioMetricCoverageIssue(
    metrics.features as Feature<RegionProperties>[],
    baselineRegionFeatures
  );
  if (issue) throw new Error(`Scenario metrics are unavailable: ${issue}`);
  proposedStations = proposed as FeatureCollection<StationProperties>;
  scenarioMetricFeatures = metrics.features as Feature<RegionProperties>[];
  renderProposedStations();
  applyScenarioView(scenarioViewMode);
  setScenarioAvailability(true, successMessage ?? "Scenario is ready. Indicators update after every edit.");
}

async function removeScenarioStation(id: string, requestId: string): Promise<void> {
  if (!id) return;
  setScenarioStatus("Removing proposed station…");
  try {
    await scenarioClient.remove(id);
    forgetOwnedRequestId(requestId);
    await refreshScenarioData("Station removed and district indicators recalculated.");
  } catch (error) {
    setScenarioStatus(error instanceof Error ? error.message : "Could not remove the station", true);
  }
}

function beginAddScenarioStation(trigger?: HTMLElement, keyboardEntry = false): void {
  if (!scenarioServiceAvailable) {
    setScenarioAvailability(false, "the scenario service is not available");
    return;
  }
  scenarioDialogTrigger = trigger ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
  scenarioKeyboardLocationEntry = keyboardEntry;
  if (keyboardEntry) {
    openScenarioDialog(map.getCenter());
    setScenarioStatus("Enter a location with latitude and longitude, then complete the proposed-station form.");
    return;
  }
  awaitingMapClick = true;
  map.getContainer().classList.add("scenario-pick-mode");
  setScenarioStatus("Click a location inside NRW for the proposed station.");
}

function closeScenarioDialog(): void {
  if (!scenarioDialogState.close()) {
    setText("scenario-form-error", "Saving is still in progress. This form will stay open until the result is confirmed.");
    return;
  }
  const modal = document.getElementById("scenario-modal") as HTMLElement | null;
  if (modal) modal.hidden = true;
  pendingLocation = null;
  scenarioKeyboardLocationEntry = false;
  awaitingMapClick = false;
  map.getContainer().classList.remove("scenario-pick-mode");
  setText("scenario-form-error", "");
  const focusTarget = scenarioDialogTrigger?.isConnected
    ? scenarioDialogTrigger
    : document.getElementById("scenario-add-button");
  scenarioDialogTrigger = null;
  focusTarget?.focus();
}

function openScenarioDialog(location: L.LatLng): void {
  if (!scenarioDialogState.beginDialog()) return;
  pendingLocation = location;
  setText("scenario-location", `${location.lat.toFixed(6)}, ${location.lng.toFixed(6)}`);
  const latitude = document.getElementById("scenario-latitude") as HTMLInputElement | null;
  const longitude = document.getElementById("scenario-longitude") as HTMLInputElement | null;
  if (latitude) latitude.value = location.lat.toFixed(6);
  if (longitude) longitude.value = location.lng.toFixed(6);
  const modal = document.getElementById("scenario-modal") as HTMLElement | null;
  if (modal) modal.hidden = false;
  window.requestAnimationFrame(() => {
    const firstField = scenarioKeyboardLocationEntry ? "scenario-latitude" : "scenario-name";
    (document.getElementById(firstField) as HTMLInputElement | null)?.focus();
  });
}

map.on("click", (event) => {
  if (!awaitingMapClick) return;
  awaitingMapClick = false;
  map.getContainer().classList.remove("scenario-pick-mode");
  openScenarioDialog(event.latlng);
});

async function boot(): Promise<void> {
  renderRegionDetail(null);
  const manifestPath = "data/manifest.json";
  const [regionsResult, stationsResult] = await Promise.all([
    readLayer(geoserverWfsUrl(runtimeConfig.geonodeRegionsLayer), "data/nrw_regions_sample.geojson", CANONICAL_DISTRICT_FIELDS, { manifestPath }),
    readLayer(geoserverWfsUrl(runtimeConfig.geonodeStationsLayer), "data/nrw_charging_stations_sample.geojson", CANONICAL_STATION_FIELDS, { manifestPath })
  ]);
  Object.assign(layerStates, {
    districts: regionsResult,
    stations: stationsResult
  });

  baselineRegionFeatures = (regionsResult.data?.features ?? []) as Feature<RegionProperties>[];
  regionFeatures = baselineRegionFeatures;
  officialStations = (stationsResult.data as FeatureCollection<StationProperties> | undefined)
    ?? { type: "FeatureCollection", features: [] };
  refreshZoomLayers();
  restoreOverlayOrder();
  renderAnalytics();
  renderProposedStations();
  renderReadStates();
  map.fitBounds(regionLayer.getBounds().isValid() ? regionLayer.getBounds() : NRW_BOUNDS, { padding: [24, 24] });
  try {
    await refreshScenarioData();
  } catch (error) {
    console.warn("Scenario layers are not available", error);
    setScenarioAvailability(false, error instanceof Error ? error.message : "the scenario layers could not be read");
  }
  void loadOptionalOverlays(manifestPath);
  void loadScoreModel(manifestPath);
}

async function loadScoreModel(manifestPath: string): Promise<void> {
  try {
    const response = await fetch(manifestPath, { cache: "no-store", credentials: "same-origin" });
    if (response.ok) {
      const manifest: unknown = await response.json();
      const candidate = manifest && typeof manifest === "object"
        ? (manifest as { score_model?: unknown }).score_model
        : undefined;
      if (Array.isArray(candidate)) scoreModel = publishedScoreModelTerms(manifest) as ScoreModelTerm[];
    }
  } catch {
    // A live district layer can still use the published read-only score model.
  }
  if (!scoreModel.length) {
    try {
      const response = await fetch(geoserverWfsUrl(runtimeConfig.scoreModelLayer), { cache: "no-store", credentials: "same-origin" });
      if (response.ok) {
        const payload: unknown = await response.json();
        scoreModel = publishedScoreModelTerms(payload) as ScoreModelTerm[];
      }
    } catch {
      // The evidence panel remains explicit when no published bounds are readable.
    }
  }
  const selected = regionFeatures.find((feature) => regionId(feature.properties) === selectedRegionId) ?? null;
  if (selected) renderRegionDetail(selected);
}

async function loadOptionalOverlays(manifestPath: string): Promise<void> {
  const [renewableResult, regionalRoadResult, autobahnResult] = await Promise.all([
    // Optional overlays have manifest-validated canonical snapshots. Selecting
    // those avoids heavyweight GeoServer overlay queries competing with the
    // required baseline and scenario reads.
    readLayer(geoserverWfsUrl(runtimeConfig.renewableAssetsLayer), "data/nrw_renewable_assets_sample.geojson", ["source_id", "technology", "capacity_mw", "status"], { manifestPath, preferSnapshot: true }),
    readLayer(geoserverWfsUrl(runtimeConfig.regionalRoadsLayer), "data/nrw_regional_roads_sample.geojson", ["source_id"], { manifestPath, preferSnapshot: true }),
    readLayer(geoserverWfsUrl(runtimeConfig.autobahnsLayer), "data/nrw_autobahns_sample.geojson", ["source_id"], { manifestPath, preferSnapshot: true })
  ]);
  Object.assign(layerStates, {
    renewables: renewableResult,
    "regional roads": regionalRoadResult,
    autobahns: autobahnResult
  });
  if (regionalRoadResult.data) regionalRoadLayer.addData(regionalRoadResult.data as FeatureCollection<RoadProperties>);
  if (autobahnResult.data) autobahnFeatures = autobahnResult.data as FeatureCollection<RoadProperties>;
  renewableAssets = (renewableResult.data as FeatureCollection<RenewableProperties> | undefined)
    ?? { type: "FeatureCollection", features: [] };
  refreshZoomLayers();
  restoreOverlayOrder();
  renderReadStates();
}

function chooseIndicator(metric: ScoreMetric): void {
  activeScore = metric;
  (document.getElementById("score-mode") as HTMLSelectElement).value = metric;
  document.querySelectorAll<HTMLButtonElement>("[data-question]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.question === metric));
  });
  renderQuestionExplanation(metric);
  regionLayer.setStyle((feature) => regionStyle(feature as Feature<RegionProperties>));
  renderRanking(metric);
  const selected = regionFeatures.find((feature) => regionId(feature.properties) === selectedRegionId) ?? null;
  if (selected) renderRegionDetail(selected);
}

function renderQuestionExplanation(metric: ScoreMetric = activeScore): void {
  if (scenarioViewMode === "change") {
    setText("question-explanation", `${scoreLabels[metric]} change is scenario minus baseline in score points. Signed changes are ordered from increases through unchanged values to decreases; they are not current scores or a delta rank.`);
    return;
  }
  const explanations: Record<ScoreMetric, string> = {
    chargerDeficitScore: "Most underserved means the largest charging gap: 100 − EV Readiness. Readiness considers charging-point density, proximity and charging points relative to population. Check the data notice for calculation limits.",
    investmentPriorityScore: "Highest priority combines 60% charging deficit with 40% infrastructure opportunity. It can differ from the most underserved district and does not identify a construction-ready site.",
    evReadinessScore: "Higher EV Readiness means stronger present charging provision relative to other districts. This is not a forecast of future demand.",
    infrastructureOpportunityScore: "Higher Infrastructure Opportunity means stronger supporting traffic, grid and renewable context. Grid capacity is a proxy, not a confirmed connection offer."
  };
  setText("question-explanation", explanations[metric]);
}

document.querySelectorAll<HTMLInputElement>('input[name="map-style"]').forEach((input) => {
  input.addEventListener("change", () => {
    if (!input.checked) return;
    illuminatedMap = input.value === "illuminated";
    map.getContainer().classList.toggle("map-illuminated", illuminatedMap);
    chooseIndicator(activeScore);
  });
});

document.getElementById("score-mode")?.addEventListener("change", (event) => {
  chooseIndicator((event.target as HTMLSelectElement).value as ScoreMetric);
});
document.querySelectorAll<HTMLButtonElement>(".ranking-tab, [data-question]").forEach((button) => {
  button.addEventListener("click", () => chooseIndicator((button.dataset.question ?? button.dataset.ranking) as ScoreMetric));
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

document.getElementById("scenario-add-button")?.addEventListener("click", (event) => beginAddScenarioStation(event.currentTarget as HTMLElement, event.detail === 0));
document.getElementById("map-add-station")?.addEventListener("click", (event) => beginAddScenarioStation(event.currentTarget as HTMLElement, event.detail === 0));
document.getElementById("scenario-modal-close")?.addEventListener("click", closeScenarioDialog);
document.getElementById("scenario-cancel")?.addEventListener("click", closeScenarioDialog);

document.getElementById("scenario-modal")?.addEventListener("keydown", (event) => {
  const modal = event.currentTarget as HTMLElement;
  if (event.key === "Escape") {
    event.preventDefault();
    closeScenarioDialog();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...modal.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]'
  )].filter((element) => !element.hidden && element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

document.getElementById("scenario-reset-button")?.addEventListener("click", async () => {
  const owned = proposedStations.features.filter((feature) => {
    const requestId = proposalRequestId(feature);
    return requestId !== null && ownedRequestIds.has(requestId);
  });
  if (!owned.length) {
    setScenarioStatus("No proposals created in this browser can be cleared. Other saved proposals are preserved.");
    return;
  }
  if (!window.confirm("Remove this browser's proposed charging stations? Other saved proposals will remain.")) return;
  setScenarioStatus("Removing this browser's proposed stations…");
  try {
    const requestIdByStationId = new Map(owned.map((feature) => [proposedStationId(feature), proposalRequestId(feature)]));
    const ids = [...requestIdByStationId.keys()].filter(Boolean);
    await scenarioClient.reset(ids, (id) => forgetOwnedRequestId(requestIdByStationId.get(id) ?? null));
    applyScenarioView("baseline");
    await refreshScenarioData("This browser's proposals were removed. Other saved proposals were preserved.");
  } catch (error) {
    // A sequential reset may have already removed some owned rows. Refresh so
    // the visible list and ownership state retain that completed progress.
    try {
      await refreshScenarioData();
    } catch {
      // The original reset error is the actionable one.
    }
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
  const maxPointPowerKw = Number((document.getElementById("scenario-max-point-power") as HTMLInputElement).value);
  const latitude = Number((document.getElementById("scenario-latitude") as HTMLInputElement).value);
  const longitude = Number((document.getElementById("scenario-longitude") as HTMLInputElement).value);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    setText("scenario-form-error", "Enter a latitude and longitude inside NRW before saving a proposal.");
    return;
  }
  const enteredLocation = L.latLng(latitude, longitude);
  if (!L.latLngBounds(NRW_BOUNDS).contains(enteredLocation)) {
    setText("scenario-form-error", "Enter a latitude and longitude inside NRW before saving a proposal.");
    return;
  }
  pendingLocation = enteredLocation;
  if (submit) submit.disabled = true;
  setText("scenario-form-error", "");
  try {
    const { operation, saved } = await submitScenarioProposal({
      client: scenarioClient,
      dialogState: scenarioDialogState,
      createRequestId: requestIdForNewProposal,
      input: {
        name,
        chargingPoints,
        powerKw,
        maxPointPowerKw,
        longitude: pendingLocation.lng,
        latitude: pendingLocation.lat
      }
    });
    rememberOwnedRequestId(operation.requestId);
    setScenarioStatus(saved.reconciled
      ? "Station was already saved and has been safely reconciled. Refreshing indicators…"
      : "Station saved. Refreshing indicators…");
    applyScenarioView("scenario");
    try {
      await refreshScenarioData("Station added and district indicators recalculated.");
      scenarioDialogState.finishSubmission();
      closeScenarioDialog();
      form.reset();
    } catch (refreshError) {
      const detail = refreshError instanceof Error ? refreshError.message : "the scenario data could not be refreshed";
      scenarioDialogState.finishSubmission({ needsReconciliation: true });
      setScenarioStatus(`Station saved; refresh pending: ${detail}. Retry “Add and recalculate” after recovery.`, true);
      setText("scenario-form-error", "Station saved, but indicators could not refresh. Your form values are retained; retrying is safe.");
    }
  } catch (error) {
    setText("scenario-form-error", error instanceof Error ? error.message : "Could not add the station");
  } finally {
    if (submit) submit.disabled = false;
  }
});

boot().catch((error) => {
  console.error(error);
  setText("data-quality-note", "Data could not be loaded. District comparisons are unavailable; try again when the data service is available.");
  const note = document.querySelector(".map-note");
  if (note) {
    note.textContent =
      "Map data is unavailable. Reload the page once the data service is available.";
  }
});
