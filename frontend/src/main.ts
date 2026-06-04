import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "../style.css";

type ScoreMetric =
  | "investmentPriorityScore"
  | "evReadinessScore"
  | "gridReadinessScore"
  | "chargerDeficitScore";

type RegionProperties = {
  id: string;
  name: string;
  state: string;
  population: number;
  areaKm2: number;
  box: [number, number, number, number];
  stationCount: number;
  chargerDensity: number;
  chargersPerKm2: number;
  averageDistanceKm: number;
  substationCount: number;
  demandScore: number;
  evReadinessScore: number;
  gridReadinessScore: number;
  chargerDeficitScore: number;
  investmentPriorityScore: number;
};

type StationProperties = {
  name: string;
  operator: string;
  region: string;
  power_kw: number;
  connectors: number;
  status: string;
};

type Feature<T> = GeoJSON.Feature<GeoJSON.Geometry, T>;
type FeatureCollection<T> = GeoJSON.FeatureCollection<GeoJSON.Geometry, T>;

const datasets = [
  {
    id: "stations",
    title: "EV Charging Stations Germany",
    type: "Point GeoJSON",
    file: "data/ev_charging_stations_sample.geojson",
    service: "GeoServer WFS: geonode:ev_charging_stations_germany",
    note: "Real replacement: Bundesnetzagentur Ladesaeulenregister"
  },
  {
    id: "regions",
    title: "NUTS-3 / Planning Regions",
    type: "Polygon GeoJSON",
    file: "data/germany_regions_sample.geojson",
    service: "GeoServer WMS/WFS: geonode:germany_regions",
    note: "Real replacement: Eurostat GISCO NUTS-3 filtered to Germany"
  },
  {
    id: "metrics",
    title: "Regional Energy Scores",
    type: "Derived metrics",
    file: "data/processed/nuts3_charger_metrics.geojson",
    service: "GeoNode REST API and PostGIS materialized views",
    note: "Scores should be produced by the pandas/geopandas pipeline"
  }
];

const scoreLabels: Record<ScoreMetric, string> = {
  investmentPriorityScore: "Investment Priority Score",
  evReadinessScore: "EV Readiness Score",
  gridReadinessScore: "Grid Readiness Score",
  chargerDeficitScore: "Charger Deficit Score"
};

let activeScore: ScoreMetric = "investmentPriorityScore";
let activeRanking: ScoreMetric = "investmentPriorityScore";
let regionFeatures: Feature<RegionProperties>[] = [];
let selectedRegionId: string | null = null;
let selectedLayer: L.Layer | null = null;

const map = L.map("map", { preferCanvas: true, zoomControl: true }).setView([51.1, 10.2], 6);

const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap"
}).addTo(map);

const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 20,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
});

const stationLayer = L.geoJSON(undefined, {
  pointToLayer: (_feature, latlng) =>
    L.circleMarker(latlng, {
      radius: 2.2,
      color: "#8a2b09",
      weight: 0.5,
      fillColor: "#d9480f",
      fillOpacity: 0.65
    }),
  onEachFeature: (feature, layer) => {
    const p = feature.properties as StationProperties;
    layer.bindPopup(`
      <div class="station-popup">
        <h3>${p.name}</h3>
        <p><strong>Operator:</strong> ${p.operator}</p>
        <p><strong>Region:</strong> ${p.region}</p>
        <p><strong>Power:</strong> ${p.power_kw} kW</p>
        <p><strong>Connectors:</strong> ${p.connectors}</p>
        <p><strong>Status:</strong> ${p.status}</p>
      </div>
    `);
  }
}).addTo(map);

const regionLayer = L.geoJSON(undefined, {
  style: (feature) => regionStyle(feature as Feature<RegionProperties>),
  onEachFeature: (feature, layer) => {
    const regionFeature = feature as Feature<RegionProperties>;
    layer.on("click", () => selectRegion(regionFeature, layer));
    layer.bindTooltip(() => {
      const p = regionFeature.properties;
      return `${p.name}: ${p[activeScore]} (${scoreLabels[activeScore]})`;
    }, { sticky: true });
  }
}).addTo(map);

L.control
  .layers(
    {
      OpenStreetMap: osm,
      "Carto Light": carto
    },
    {
      "EV charging stations": stationLayer,
      "Administrative regions": regionLayer
    },
    { collapsed: false }
  )
  .addTo(map);

function setText(id: string, value: string | number): void {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value);
}

function scoreColor(score: number): string {
  if (score >= 80) return "#0f766e";
  if (score >= 65) return "#4d9f75";
  if (score >= 50) return "#c0a33c";
  if (score >= 35) return "#d97706";
  return "#b91c1c";
}

function regionStyle(feature: Feature<RegionProperties>): L.PathOptions {
  const score = feature.properties[activeScore];
  return {
    color: selectedRegionId === feature.properties.id ? "#0b5f59" : "#334155",
    weight: selectedRegionId === feature.properties.id ? 3 : 1.1,
    fillColor: scoreColor(score),
    fillOpacity: 0.55
  };
}

function boundsFromBox([west, south, east, north]: [number, number, number, number]): L.LatLngBoundsExpression {
  return [
    [south, west],
    [north, east]
  ];
}

function sortedRegions(metric: ScoreMetric): Feature<RegionProperties>[] {
  return [...regionFeatures].sort((a, b) => b.properties[metric] - a.properties[metric]);
}

function renderDatasets(): void {
  const list = document.getElementById("dataset-list");
  if (!list) return;
  list.innerHTML = "";

  datasets.forEach((dataset, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `dataset-button${index === 0 ? " active" : ""}`;
    button.innerHTML = `<strong>${dataset.title}</strong><span>${dataset.type}</span>`;
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
    <div class="metadata-row"><span>Title</span><strong>${dataset.title}</strong></div>
    <div class="metadata-row"><span>Dataset type</span><strong>${dataset.type}</strong></div>
    <div class="metadata-row"><span>Prototype file</span><strong>${dataset.file}</strong></div>
    <div class="metadata-row"><span>Intended service</span><strong>${dataset.service}</strong></div>
    <div class="metadata-row"><span>Data note</span><strong>${dataset.note}</strong></div>
  `;
}

function renderKpis(stations: FeatureCollection<StationProperties>): void {
  const averageDensity =
    regionFeatures.reduce((sum, feature) => sum + feature.properties.chargerDensity, 0) / regionFeatures.length;
  const best = sortedRegions("evReadinessScore")[0].properties;
  const underserved = sortedRegions("chargerDeficitScore")[0].properties;
  const priority = sortedRegions("investmentPriorityScore")[0].properties;

  setText("kpi-total-stations", new Intl.NumberFormat("en-US").format(stations.features.length));
  setText("kpi-average-density", `${averageDensity.toFixed(1)} / 10k`);
  setText("kpi-best-region", best.name);
  setText("kpi-underserved-region", underserved.name);
  setText("kpi-priority-region", priority.name);
}

function renderRegionDetail(feature: Feature<RegionProperties> | null): void {
  const detail = document.getElementById("region-detail");
  if (!detail) return;

  if (!feature) {
    detail.innerHTML =
      '<p class="empty-state">Select a region to inspect charger supply, grid readiness, demand, and priority.</p>';
    return;
  }

  const p = feature.properties;
  const priorityRank = sortedRegions("investmentPriorityScore").findIndex((item) => item.properties.id === p.id) + 1;
  const recommendation =
    p.investmentPriorityScore >= 78
      ? "High priority. Demand is strong and charger deficit is visible, so this region should be checked for fast-charging rollout."
      : p.evReadinessScore >= 75
        ? "Prepared region. It is more useful as benchmark and corridor reliability case."
        : "Medium case. It needs more validation with population and road data before investment decision.";

  detail.innerHTML = `
    <div class="detail-title">
      <h3>${p.name}</h3>
      <span class="score-pill" style="background:${scoreColor(p.investmentPriorityScore)}">${p.investmentPriorityScore}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-metric"><span>Stations</span><strong>${p.stationCount.toLocaleString("en-US")}</strong></div>
      <div class="detail-metric"><span>Per 10k residents</span><strong>${p.chargerDensity}</strong></div>
      <div class="detail-metric"><span>EV readiness</span><strong>${p.evReadinessScore}</strong></div>
      <div class="detail-metric"><span>Grid readiness</span><strong>${p.gridReadinessScore}</strong></div>
      <div class="detail-metric"><span>Priority score</span><strong>${p.investmentPriorityScore}</strong></div>
      <div class="detail-metric"><span>Priority rank</span><strong>#${priorityRank}</strong></div>
      <div class="detail-metric"><span>Avg station distance</span><strong>${p.averageDistanceKm} km</strong></div>
      <div class="detail-metric"><span>Substations</span><strong>${p.substationCount}</strong></div>
    </div>
    <p class="recommendation">${recommendation}</p>
  `;
}

function selectRegion(feature: Feature<RegionProperties>, layer?: L.Layer): void {
  selectedRegionId = feature.properties.id;
  if (selectedLayer) regionLayer.resetStyle(selectedLayer);

  selectedLayer = layer ?? null;
  if (!selectedLayer) {
    regionLayer.eachLayer((candidate) => {
      const candidateFeature = (candidate as L.Layer & { feature?: Feature<RegionProperties> }).feature;
      if (candidateFeature?.properties.id === selectedRegionId) selectedLayer = candidate;
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
      const item = document.createElement("li");
      item.className = "ranking-item";
      item.innerHTML = `
        <span class="ranking-rank">#${index + 1}</span>
        <span class="ranking-name" title="${p.name}">${p.name}</span>
        <span class="score-pill" style="background:${scoreColor(p[metric])}">${p[metric]}</span>
      `;
      item.addEventListener("click", () => {
        selectRegion(feature);
        map.fitBounds(boundsFromBox(p.box), { padding: [24, 24], maxZoom: 8 });
      });
      list.appendChild(item);
    });
}

async function fetchGeoJson<T>(path: string): Promise<FeatureCollection<T>> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Could not load ${path}`);
  return response.json();
}

async function boot(): Promise<void> {
  renderDatasets();
  renderRegionDetail(null);

  const [regions, stations] = await Promise.all([
    fetchGeoJson<RegionProperties>("data/germany_regions_sample.geojson"),
    fetchGeoJson<StationProperties>("data/ev_charging_stations_sample.geojson")
  ]);

  regionFeatures = regions.features as Feature<RegionProperties>[];
  regionLayer.addData(regions);
  stationLayer.addData(stations);
  renderKpis(stations);
  renderRanking();
  map.fitBounds(regionLayer.getBounds(), { padding: [24, 24] });
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
  if (note) note.textContent = "Data could not be loaded. Run the TypeScript frontend with npm run dev from frontend/.";
});
