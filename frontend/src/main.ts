import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "../style.css";

(window as Window & { __energyAppBooted?: boolean }).__energyAppBooted = true;

type ScoreMetric =
  | "investmentPriorityScore"
  | "chargingSupplyScore"
  | "chargerDeficitScore"
  | "dataQualityScore";

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
  chargingSupplyScore?: number;
  charging_supply_score?: number;
  chargerDeficitScore?: number;
  charger_deficit_score?: number;
  investmentPriorityScore?: number;
  investment_priority_score?: number;
  priorityRank?: number;
  priority_rank?: number;
  priorityTier?: string;
  priority_tier?: string;
  dataQualityScore?: number;
  data_quality_score?: number;
  dataQualityFlag?: string;
  data_quality_flag?: string;
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
  geonodeRegionsLayer: "geonode:nrw_nuts3_districts"
};

const runtimeConfig = (() => {
  const config = (window as Window & { __energyAppConfig?: Partial<RuntimeConfig> }).__energyAppConfig;
  return { ...DEFAULT_RUNTIME_CONFIG, ...config };
})();

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
    note: "Current scores use charger supply only until population, roads, grid and renewable layers are connected"
  }
];

const scoreLabels: Record<ScoreMetric, string> = {
  investmentPriorityScore: "Investment Priority",
  chargingSupplyScore: "Charging Supply",
  chargerDeficitScore: "Charger Deficit",
  dataQualityScore: "Data Quality"
};

const scoreKeys: Record<ScoreMetric, string[]> = {
  investmentPriorityScore: ["investmentPriorityScore", "investment_priority_score"],
  chargingSupplyScore: ["chargingSupplyScore", "charging_supply_score"],
  chargerDeficitScore: ["chargerDeficitScore", "charger_deficit_score"],
  dataQualityScore: ["dataQualityScore", "data_quality_score"]
};

let activeScore: ScoreMetric = "investmentPriorityScore";
let activeRanking: ScoreMetric = "investmentPriorityScore";
let regionFeatures: Feature<RegionProperties>[] = [];
let selectedRegionId: string | null = null;
let selectedLayer: L.Layer | null = null;

const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(NRW_CENTER, 7);
map.createPane("regions");
map.createPane("stations");
map.getPane("regions")!.style.zIndex = "410";
map.getPane("stations")!.style.zIndex = "460";

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

function formatScore(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
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

function scoreColor(score: number): string {
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
    fillColor: scoreColor(score),
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
  return [...regionFeatures].sort((a, b) => scoreValue(b.properties, metric) - scoreValue(a.properties, metric));
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

function renderKpis(stations: FeatureCollection<StationProperties>): void {
  const supplyScores = regionFeatures.map((feature) => scoreValue(feature.properties, "chargingSupplyScore"));
  const averageSupply = supplyScores.reduce((sum, score) => sum + score, 0) / Math.max(1, supplyScores.length);
  const bestSupply = sortedRegions("chargingSupplyScore")[0]?.properties;
  const underserved = sortedRegions("chargerDeficitScore")[0]?.properties;
  const priority = sortedRegions("investmentPriorityScore")[0]?.properties;

  setText("kpi-total-stations", new Intl.NumberFormat("en-US").format(stations.features.length));
  setText("kpi-average-density", `${averageSupply.toFixed(0)} / 100`);
  setText("kpi-best-region", bestSupply ? regionName(bestSupply) : "-");
  setText("kpi-underserved-region", underserved ? regionName(underserved) : "-");
  setText("kpi-priority-region", priority ? regionName(priority) : "-");
}

function renderRegionDetail(feature: Feature<RegionProperties> | null): void {
  const detail = document.getElementById("region-detail");
  if (!detail) return;

  if (!feature) {
    detail.innerHTML =
      '<p class="empty-state">Select a NRW district to inspect charging supply, deficit, priority rank and data gaps.</p>';
    return;
  }

  const p = feature.properties;
  const investment = scoreValue(p, "investmentPriorityScore");
  const supply = scoreValue(p, "chargingSupplyScore");
  const deficit = scoreValue(p, "chargerDeficitScore");
  const quality = scoreValue(p, "dataQualityScore");
  const rank = numericValue(p, ["priorityRank", "priority_rank"], sortedRegions("investmentPriorityScore").findIndex((item) => regionId(item.properties) === regionId(p)) + 1);
  const tier = textValue(p, ["priorityTier", "priority_tier"], "screening");
  const recommendation =
    investment >= 70
      ? "High screening priority. Charger supply is weak relative to other NRW districts, so this district should be checked first once demand and road layers are joined."
      : supply >= 70
        ? "Well supplied in the current charger-only POC. Use it as a benchmark, not as final investment proof."
        : "Medium screening case. It needs population, road and grid data before a real investment decision.";

  detail.innerHTML = `
    <div class="detail-title">
      <h3>${escapeHtml(regionName(p))}</h3>
      <span class="score-pill" style="background:${scoreColor(investment)}">${formatScore(investment)}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-metric"><span>NUTS-3</span><strong>${escapeHtml(textValue(p, ["nuts_code"], "-"))}</strong></div>
      <div class="detail-metric"><span>Stations</span><strong>${formatNumber(numericValue(p, ["stationCount", "chargers_total"], 0))}</strong></div>
      <div class="detail-metric"><span>Charging points</span><strong>${formatNumber(numericValue(p, ["charging_points_total"], 0))}</strong></div>
      <div class="detail-metric"><span>Fast chargers</span><strong>${formatNumber(numericValue(p, ["fast_chargers", "fast_chargers_total"], 0))}</strong></div>
      <div class="detail-metric"><span>Charging supply</span><strong>${formatScore(supply)}</strong></div>
      <div class="detail-metric"><span>Charger deficit</span><strong>${formatScore(deficit)}</strong></div>
      <div class="detail-metric"><span>Priority rank</span><strong>#${formatNumber(rank)}</strong></div>
      <div class="detail-metric"><span>Data quality</span><strong>${formatScore(quality)}</strong></div>
    </div>
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
      const width = Math.max(8, Math.min(100, score));
      const item = document.createElement("li");
      item.className = "ranking-item";
      item.innerHTML = `
        <span class="ranking-rank">#${index + 1}</span>
        <span class="ranking-dot"></span>
        <span class="ranking-name" title="${escapeHtml(regionName(p))}">${escapeHtml(regionName(p))}</span>
        <span class="ranking-bar"><i style="width:${width}%"></i></span>
        <span class="score-pill" style="background:${scoreColor(score)}">${formatScore(score)}</span>
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
  regionLayer.addData(regions);
  stationLayer.addData(stations);
  renderKpis(stations);
  renderRanking();
  setHeaderStatus(`${regionsResult.source} | ${stationsResult.source}`);
  const note = document.querySelector(".map-note");
  if (note) {
    note.textContent =
      regionsResult.source.startsWith("GeoNode") || stationsResult.source.startsWith("GeoNode")
        ? "GeoNode-connected preview: regions and/or charging stations are being served from GeoServer WFS, with local GeoJSON fallback if the stack is offline."
        : "Local-first preview: GeoNode is offline, so the dashboard is using the checked-in NRW GeoJSON snapshot.";
  }
  map.fitBounds(regionLayer.getBounds().isValid() ? regionLayer.getBounds() : NRW_BOUNDS, { padding: [24, 24] });
}

document.getElementById("score-mode")?.addEventListener("change", (event) => {
  activeScore = (event.target as HTMLSelectElement).value as ScoreMetric;
  regionLayer.setStyle((feature) => regionStyle(feature as Feature<RegionProperties>));
});

document.querySelectorAll(".ranking-tab").forEach((button) => {
  button.addEventListener("click", () => renderRanking((button as HTMLElement).dataset.ranking as ScoreMetric));
});

boot().catch((error) => {
  console.error(error);
  const note = document.querySelector(".map-note");
  if (note) {
    note.textContent =
      "NRW GeoJSON and GeoNode WFS could not be loaded. Check the local GeoNode stack or run `python3 -m http.server 8000 --directory frontend`.";
  }
});
