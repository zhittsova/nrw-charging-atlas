const DATASETS = [
  {
    id: "stations",
    title: "EV Charging Stations Germany",
    type: "Point GeoJSON",
    file: "data/ev_charging_stations_sample.geojson",
    source: "Synthetic Germany-wide sample modeled after public charging infrastructure fields",
    servicePath: "GeoServer WFS: geonode:ev_charging_stations_germany",
    description:
      "Existing EV charging points used for charger density, accessibility, underserved-region detection, and investment priority scoring."
  },
  {
    id: "regions",
    title: "German Administrative Regions",
    type: "Polygon GeoJSON",
    file: "data/germany_regions_sample.geojson",
    source: "Simplified synthetic NUTS3-style planning regions for MVP analytics",
    servicePath: "GeoServer WMS/WFS: geonode:germany_regions",
    description:
      "Regional units used for district-level scoring, rankings, choropleth analysis, and investment recommendations."
  },
  {
    id: "grid",
    title: "Energy Grid Infrastructure",
    type: "Point GeoJSON",
    file: "generated in frontend/app.js",
    source: "Synthetic substations and grid-accessibility proxies",
    servicePath: "GeoServer WFS: geonode:grid_substations",
    description:
      "Substation and grid-readiness indicators used to estimate electrification preparedness and infrastructure feasibility."
  },
  {
    id: "analytics",
    title: "Composite Planning Scores",
    type: "Derived indicators",
    file: "generated in frontend/app.js",
    source: "Calculated from charger, grid, demand, and road-accessibility proxies",
    servicePath: "GeoNode REST API: /api/v2/datasets and derived analytics endpoint",
    description:
      "EV Readiness, Grid Readiness, Charger Deficit, and Investment Priority scores for decision support."
  }
];

const REGION_SAMPLE = [
  { id: "de-berlin", name: "Berlin", state: "Berlin", population: 3769000, areaKm2: 891, box: [13.08, 52.35, 13.76, 52.68], supply: 7.2, grid: 84, road: 88, renewable: 44, demand: 92 },
  { id: "de-hamburg", name: "Hamburg", state: "Hamburg", population: 1899000, areaKm2: 755, box: [9.70, 53.39, 10.33, 53.74], supply: 6.8, grid: 82, road: 86, renewable: 50, demand: 86 },
  { id: "de-munich", name: "Munich Region", state: "Bavaria", population: 2980000, areaKm2: 5500, box: [11.05, 47.82, 12.05, 48.45], supply: 8.4, grid: 79, road: 85, renewable: 68, demand: 91 },
  { id: "de-cologne", name: "Cologne-Bonn Region", state: "North Rhine-Westphalia", population: 3560000, areaKm2: 7300, box: [6.55, 50.55, 7.45, 51.15], supply: 5.2, grid: 76, road: 90, renewable: 42, demand: 89 },
  { id: "de-frankfurt", name: "Frankfurt Rhine-Main", state: "Hesse", population: 5810000, areaKm2: 14800, box: [7.85, 49.65, 9.15, 50.45], supply: 6.1, grid: 86, road: 92, renewable: 47, demand: 94 },
  { id: "de-stuttgart", name: "Stuttgart Region", state: "Baden-Wurttemberg", population: 2800000, areaKm2: 3650, box: [8.75, 48.45, 9.65, 49.05], supply: 7.8, grid: 81, road: 87, renewable: 58, demand: 88 },
  { id: "de-ruhr", name: "Ruhr Area", state: "North Rhine-Westphalia", population: 5100000, areaKm2: 4435, box: [6.55, 51.18, 7.65, 51.75], supply: 4.7, grid: 83, road: 91, renewable: 38, demand: 95 },
  { id: "de-duesseldorf", name: "Dusseldorf-Lower Rhine", state: "North Rhine-Westphalia", population: 2260000, areaKm2: 5300, box: [6.20, 51.00, 6.95, 51.55], supply: 5.8, grid: 80, road: 88, renewable: 40, demand: 84 },
  { id: "de-hannover", name: "Hannover Region", state: "Lower Saxony", population: 1160000, areaKm2: 2290, box: [9.40, 52.15, 10.05, 52.60], supply: 5.4, grid: 74, road: 77, renewable: 63, demand: 70 },
  { id: "de-bremen", name: "Bremen-Oldenburg", state: "Bremen / Lower Saxony", population: 1760000, areaKm2: 7500, box: [8.30, 52.85, 9.15, 53.45], supply: 4.2, grid: 70, road: 75, renewable: 72, demand: 73 },
  { id: "de-leipzig", name: "Leipzig Region", state: "Saxony", population: 1050000, areaKm2: 3900, box: [11.85, 51.05, 12.75, 51.55], supply: 4.8, grid: 69, road: 76, renewable: 59, demand: 68 },
  { id: "de-dresden", name: "Dresden Region", state: "Saxony", population: 1340000, areaKm2: 6200, box: [12.80, 50.75, 14.10, 51.25], supply: 5.0, grid: 71, road: 72, renewable: 55, demand: 66 },
  { id: "de-nuremberg", name: "Nuremberg Region", state: "Bavaria", population: 1360000, areaKm2: 7600, box: [10.65, 49.10, 11.45, 49.75], supply: 6.4, grid: 74, road: 80, renewable: 66, demand: 72 },
  { id: "de-karlsruhe", name: "Karlsruhe Technology Region", state: "Baden-Wurttemberg", population: 1250000, areaKm2: 3500, box: [8.05, 48.75, 8.75, 49.30], supply: 7.0, grid: 77, road: 82, renewable: 57, demand: 76 },
  { id: "de-mannheim", name: "Rhine-Neckar", state: "Baden-Wurttemberg / Hesse", population: 2400000, areaKm2: 5600, box: [8.15, 49.25, 9.00, 49.80], supply: 5.7, grid: 78, road: 83, renewable: 48, demand: 80 },
  { id: "de-freiburg", name: "Freiburg-South Baden", state: "Baden-Wurttemberg", population: 1150000, areaKm2: 6800, box: [7.45, 47.62, 8.35, 48.25], supply: 6.6, grid: 67, road: 68, renewable: 82, demand: 62 },
  { id: "de-saarbruecken", name: "Saarland Region", state: "Saarland", population: 990000, areaKm2: 2570, box: [6.35, 49.05, 7.25, 49.65], supply: 4.0, grid: 73, road: 74, renewable: 41, demand: 58 },
  { id: "de-mainz", name: "Mainz-Wiesbaden", state: "Rhineland-Palatinate / Hesse", population: 1020000, areaKm2: 2600, box: [7.80, 49.85, 8.55, 50.25], supply: 5.9, grid: 75, road: 81, renewable: 49, demand: 70 },
  { id: "de-muenster", name: "Muensterland", state: "North Rhine-Westphalia", population: 1640000, areaKm2: 5940, box: [7.25, 51.75, 8.25, 52.35], supply: 5.1, grid: 72, road: 76, renewable: 70, demand: 64 },
  { id: "de-bielefeld", name: "Ostwestfalen-Lippe", state: "North Rhine-Westphalia", population: 2060000, areaKm2: 6500, box: [8.15, 51.70, 9.35, 52.35], supply: 4.6, grid: 68, road: 74, renewable: 65, demand: 67 },
  { id: "de-kassel", name: "North Hesse", state: "Hesse", population: 1010000, areaKm2: 8300, box: [8.65, 50.85, 10.25, 51.55], supply: 3.7, grid: 64, road: 70, renewable: 67, demand: 55 },
  { id: "de-erfurt", name: "Thuringia Central", state: "Thuringia", population: 1080000, areaKm2: 7200, box: [10.45, 50.55, 11.75, 51.20], supply: 3.8, grid: 63, road: 69, renewable: 62, demand: 54 },
  { id: "de-magdeburg", name: "Magdeburg-Borde", state: "Saxony-Anhalt", population: 780000, areaKm2: 6400, box: [10.75, 51.85, 12.30, 52.45], supply: 3.4, grid: 66, road: 67, renewable: 78, demand: 48 },
  { id: "de-rostock", name: "Rostock Coast", state: "Mecklenburg-Vorpommern", population: 650000, areaKm2: 7100, box: [11.65, 53.75, 12.75, 54.35], supply: 3.2, grid: 61, road: 58, renewable: 84, demand: 44 },
  { id: "de-kiel", name: "Kiel-Schleswig", state: "Schleswig-Holstein", population: 970000, areaKm2: 7800, box: [9.55, 53.95, 10.70, 54.65], supply: 4.5, grid: 65, road: 63, renewable: 88, demand: 52 },
  { id: "de-schwerin", name: "West Mecklenburg", state: "Mecklenburg-Vorpommern", population: 520000, areaKm2: 7000, box: [10.65, 53.15, 11.95, 53.85], supply: 2.8, grid: 58, road: 55, renewable: 82, demand: 38 },
  { id: "de-potsdam", name: "Potsdam-Havelland", state: "Brandenburg", population: 760000, areaKm2: 6200, box: [12.55, 52.20, 13.25, 52.65], supply: 4.1, grid: 68, road: 66, renewable: 74, demand: 57 },
  { id: "de-augsburg", name: "Augsburg-Swabia", state: "Bavaria", population: 1010000, areaKm2: 5200, box: [10.45, 47.95, 11.10, 48.55], supply: 5.3, grid: 70, road: 75, renewable: 73, demand: 65 },
  { id: "de-regensburg", name: "Regensburg-Upper Palatinate", state: "Bavaria", population: 850000, areaKm2: 7200, box: [11.75, 48.75, 12.65, 49.35], supply: 4.9, grid: 69, road: 72, renewable: 77, demand: 57 },
  { id: "de-wuerzburg", name: "Wuerzburg-Franconia", state: "Bavaria", population: 920000, areaKm2: 6800, box: [9.45, 49.55, 10.30, 50.10], supply: 4.6, grid: 67, road: 73, renewable: 69, demand: 56 },
  { id: "de-trier", name: "Trier-Mosel", state: "Rhineland-Palatinate", population: 630000, areaKm2: 5100, box: [6.35, 49.45, 7.20, 50.05], supply: 3.3, grid: 60, road: 59, renewable: 60, demand: 42 },
  { id: "de-cottbus", name: "Lausitz-Cottbus", state: "Brandenburg / Saxony", population: 700000, areaKm2: 7800, box: [13.85, 51.25, 14.75, 51.95], supply: 2.9, grid: 62, road: 60, renewable: 76, demand: 43 }
];

const STATION_OPERATORS = ["Stadtwerke Mobility", "EnBW", "E.ON Drive", "Aral pulse", "Ionity", "Allego", "ChargeNow", "UrbanVolt"];
const STATION_POWERS = [11, 22, 50, 75, 120, 150, 300];
const STATION_STATUSES = ["available", "available", "available", "occupied", "maintenance"];

const scoreModeLabels = {
  investmentPriorityScore: "Investment Priority Score",
  evReadinessScore: "EV Readiness Score",
  gridReadinessScore: "Grid Readiness Score",
  chargerDeficitScore: "Charger Deficit Score"
};

let activeScoreMode = "investmentPriorityScore";
let activeRanking = "investmentPriorityScore";
let selectedRegionLayer = null;
let selectedRegionId = null;
let regionFeatures = [];
let stationFeatures = [];

function polygonFromBox([west, south, east, north]) {
  return [[[west, south], [east, south], [east, north], [west, north], [west, south]]];
}

function boundsFromBox([west, south, east, north]) {
  return [[south, west], [north, east]];
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round(value, digits = 1) {
  return Number(value.toFixed(digits));
}

function scoreColor(score) {
  if (score >= 80) return "#0f766e";
  if (score >= 65) return "#4d9f75";
  if (score >= 50) return "#c0a33c";
  if (score >= 35) return "#d97706";
  return "#b91c1c";
}

function minMaxScale(value, min, max) {
  if (max === min) return 50;
  return ((value - min) / (max - min)) * 100;
}

function buildRegionAnalytics() {
  const base = REGION_SAMPLE.map((region) => {
    const stationCount = Math.round(clamp((region.population / 10000) * region.supply, 90, 1250));
    const chargerDensity = stationCount / (region.population / 10000);
    const chargersPerKm2 = stationCount / region.areaKm2;
    const populationDensity = region.population / region.areaKm2;
    const averageDistanceKm = clamp(11 / Math.sqrt(chargersPerKm2 + 0.04) - region.road / 45, 1.2, 18);
    const substationCount = Math.round(clamp((region.grid / 100) * (region.areaKm2 / 850) + region.population / 900000, 2, 24));
    const substationDensity = substationCount / (region.areaKm2 / 1000);

    return {
      ...region,
      stationCount,
      chargerDensity,
      chargersPerKm2,
      populationDensity,
      averageDistanceKm,
      substationCount,
      substationDensity
    };
  });

  const ranges = {
    chargerDensity: [Math.min(...base.map((r) => r.chargerDensity)), Math.max(...base.map((r) => r.chargerDensity))],
    chargersPerKm2: [Math.min(...base.map((r) => r.chargersPerKm2)), Math.max(...base.map((r) => r.chargersPerKm2))],
    populationDensity: [Math.min(...base.map((r) => r.populationDensity)), Math.max(...base.map((r) => r.populationDensity))],
    averageDistanceKm: [Math.min(...base.map((r) => r.averageDistanceKm)), Math.max(...base.map((r) => r.averageDistanceKm))],
    substationDensity: [Math.min(...base.map((r) => r.substationDensity)), Math.max(...base.map((r) => r.substationDensity))]
  };

  return base.map((region) => {
    const chargerDensityScore = minMaxScale(region.chargerDensity, ...ranges.chargerDensity);
    const chargerAccessibilityScore = 100 - minMaxScale(region.averageDistanceKm, ...ranges.averageDistanceKm);
    const coverageScore = minMaxScale(region.chargersPerKm2, ...ranges.chargersPerKm2);
    const demandScore = round(0.55 * region.demand + 0.45 * minMaxScale(region.populationDensity, ...ranges.populationDensity), 1);
    const evReadinessScore = round(0.4 * chargerDensityScore + 0.3 * chargerAccessibilityScore + 0.3 * coverageScore, 1);
    const gridReadinessScore = round(0.5 * region.grid + 0.3 * minMaxScale(region.substationDensity, ...ranges.substationDensity) + 0.2 * region.renewable, 1);
    const chargerDeficitScore = round(0.55 * demandScore + 0.45 * (100 - evReadinessScore), 1);
    const investmentPriorityScore = round(0.4 * demandScore + 0.3 * chargerDeficitScore + 0.2 * gridReadinessScore + 0.1 * region.road, 1);

    return {
      ...region,
      chargerDensity: round(region.chargerDensity, 2),
      chargersPerKm2: round(region.chargersPerKm2, 3),
      averageDistanceKm: round(region.averageDistanceKm, 1),
      substationDensity: round(region.substationDensity, 2),
      demandScore,
      evReadinessScore,
      gridReadinessScore,
      chargerDeficitScore,
      investmentPriorityScore
    };
  });
}

function buildRegionsGeoJson() {
  return {
    type: "FeatureCollection",
    name: "germany_energy_regions_sample",
    features: buildRegionAnalytics().map((region) => ({
      type: "Feature",
      properties: region,
      geometry: {
        type: "Polygon",
        coordinates: polygonFromBox(region.box)
      }
    }))
  };
}

function buildStationsGeoJson(regionsGeoJson) {
  let stationIndex = 1;

  return {
    type: "FeatureCollection",
    name: "ev_charging_stations_germany_sample",
    features: regionsGeoJson.features.flatMap((feature) => {
      const region = feature.properties;
      const [west, south, east, north] = region.box;
      const columns = Math.ceil(Math.sqrt(region.stationCount * ((east - west) / (north - south))));
      const rows = Math.ceil(region.stationCount / columns);

      return Array.from({ length: region.stationCount }, (_, localIndex) => {
        const column = localIndex % columns;
        const row = Math.floor(localIndex / columns);
        const longitudeJitter = (((localIndex * 37) % 19) - 9) * 0.0014;
        const latitudeJitter = (((localIndex * 53) % 23) - 11) * 0.0011;
        const longitude = round(west + ((column + 0.5) / columns) * (east - west) + longitudeJitter, 5);
        const latitude = round(south + ((row + 0.5) / rows) * (north - south) + latitudeJitter, 5);
        const operator = STATION_OPERATORS[(stationIndex + localIndex) % STATION_OPERATORS.length];
        const power_kw = STATION_POWERS[(stationIndex + localIndex * 2) % STATION_POWERS.length];
        const status = STATION_STATUSES[(stationIndex + localIndex) % STATION_STATUSES.length];
        const id = `ev-de-${String(stationIndex).padStart(5, "0")}`;
        stationIndex += 1;

        return {
          type: "Feature",
          properties: {
            id,
            name: `${region.name} Charging Asset ${String(localIndex + 1).padStart(4, "0")}`,
            operator,
            region: region.name,
            state: region.state,
            address: `Synthetic site ${stationIndex - 1}, ${region.name}`,
            connectors: 2 + ((stationIndex + localIndex) % 7),
            power_kw,
            status,
            latitude,
            longitude
          },
          geometry: {
            type: "Point",
            coordinates: [longitude, latitude]
          }
        };
      });
    })
  };
}

function buildSubstationsGeoJson(regionsGeoJson) {
  let substationIndex = 1;

  return {
    type: "FeatureCollection",
    name: "grid_substations_germany_sample",
    features: regionsGeoJson.features.flatMap((feature) => {
      const region = feature.properties;
      const [west, south, east, north] = region.box;

      return Array.from({ length: region.substationCount }, (_, localIndex) => {
        const longitude = round(west + (((localIndex * 7) % 13) + 1) / 14 * (east - west), 5);
        const latitude = round(south + (((localIndex * 5) % 11) + 1) / 12 * (north - south), 5);
        const id = `grid-${String(substationIndex).padStart(4, "0")}`;
        substationIndex += 1;

        return {
          type: "Feature",
          properties: {
            id,
            name: `${region.name} Substation ${localIndex + 1}`,
            region: region.name,
            voltage_kv: [110, 220, 380][localIndex % 3]
          },
          geometry: {
            type: "Point",
            coordinates: [longitude, latitude]
          }
        };
      });
    })
  };
}

const SAMPLE_REGIONS = buildRegionsGeoJson();
const SAMPLE_STATIONS = buildStationsGeoJson(SAMPLE_REGIONS);
const SAMPLE_SUBSTATIONS = buildSubstationsGeoJson(SAMPLE_REGIONS);

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: true
}).setView([51.1, 10.2], 6);

const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}).addTo(map);

const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 20,
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
});

const stationLayer = L.geoJSON(null, {
  pointToLayer: (feature, latlng) =>
    L.circleMarker(latlng, {
      radius: 2.3,
      color: "#8a2b09",
      weight: 0.6,
      fillColor: "#d9480f",
      fillOpacity: 0.72
    }),
  onEachFeature: (feature, layer) => {
    const p = feature.properties;
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

const substationLayer = L.geoJSON(null, {
  pointToLayer: (feature, latlng) =>
    L.circleMarker(latlng, {
      radius: 4,
      color: "#1f2937",
      weight: 1,
      fillColor: "#fbbf24",
      fillOpacity: 0.9
    }),
  onEachFeature: (feature, layer) => {
    const p = feature.properties;
    layer.bindPopup(`
      <div class="station-popup">
        <h3>${p.name}</h3>
        <p><strong>Region:</strong> ${p.region}</p>
        <p><strong>Voltage:</strong> ${p.voltage_kv} kV</p>
      </div>
    `);
  }
});

const regionLayer = L.geoJSON(null, {
  style: regionStyle,
  onEachFeature: (feature, layer) => {
    layer.on("click", () => selectRegion(feature, layer));
    layer.bindTooltip(() => {
      const p = feature.properties;
      return `${p.name}: ${p[activeScoreMode]} (${scoreModeLabels[activeScoreMode]})`;
    }, { sticky: true });
  }
}).addTo(map);

L.control
  .layers(
    {
      "OpenStreetMap": osm,
      "Carto Light": carto
    },
    {
      "EV charging stations": stationLayer,
      "Administrative regions": regionLayer,
      "Grid substations": substationLayer
    },
    { collapsed: false }
  )
  .addTo(map);

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(value);
}

function regionStyle(feature) {
  const score = feature.properties[activeScoreMode];

  return {
    color: selectedRegionId === feature.properties.id ? "#0b5f59" : "#334155",
    weight: selectedRegionId === feature.properties.id ? 3 : 1.2,
    fillColor: scoreColor(score),
    fillOpacity: 0.54
  };
}

function sortedRegions(metric, ascending = false) {
  return [...regionFeatures].sort((a, b) => {
    const delta = a.properties[metric] - b.properties[metric];
    return ascending ? delta : -delta;
  });
}

function getRegionRank(regionId, metric) {
  return sortedRegions(metric).findIndex((feature) => feature.properties.id === regionId) + 1;
}

function buildDatasetList() {
  const list = document.getElementById("dataset-list");
  list.innerHTML = "";

  DATASETS.forEach((dataset, index) => {
    const button = document.createElement("button");
    button.className = `dataset-button${index === 0 ? " active" : ""}`;
    button.type = "button";
    button.dataset.datasetId = dataset.id;
    button.innerHTML = `<strong>${dataset.title}</strong><span>${dataset.type}</span>`;
    button.addEventListener("click", () => {
      document.querySelectorAll(".dataset-button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      renderMetadata(dataset.id);
    });
    list.appendChild(button);
  });

  renderMetadata(DATASETS[0].id);
}

function renderMetadata(datasetId) {
  const dataset = DATASETS.find((item) => item.id === datasetId);
  const content = document.getElementById("metadata-content");

  content.innerHTML = `
    <div class="metadata-row">
      <span>Title</span>
      <strong>${dataset.title}</strong>
    </div>
    <div class="metadata-row">
      <span>Dataset type</span>
      <strong>${dataset.type}</strong>
    </div>
    <div class="metadata-row">
      <span>Prototype file</span>
      <strong>${dataset.file}</strong>
    </div>
    <div class="metadata-row">
      <span>Intended service</span>
      <strong>${dataset.servicePath}</strong>
    </div>
    <div class="metadata-row">
      <span>Source note</span>
      <strong>${dataset.source}</strong>
    </div>
    <div class="metadata-row">
      <span>Description</span>
      <strong>${dataset.description}</strong>
    </div>
  `;
}

function renderRegionDetail(feature) {
  const detail = document.getElementById("region-detail");

  if (!feature) {
    detail.innerHTML = `<p class="empty-state">Select a region on the map or from a ranking to inspect charger supply, grid readiness, demand, and investment priority.</p>`;
    return;
  }

  const p = feature.properties;
  const priorityRank = getRegionRank(p.id, "investmentPriorityScore");
  const recommendation =
    p.investmentPriorityScore >= 78
      ? "High priority: strong demand and measurable charger deficit. Prioritize new public fast-charging capacity and grid coordination."
      : p.evReadinessScore >= 75
        ? "Well prepared: use as a benchmark region and focus on reliability, uptime, and targeted corridor expansion."
        : "Monitor and improve: combine incremental charger rollout with grid-accessibility and demand validation.";

  detail.innerHTML = `
    <div class="detail-title">
      <h3>${p.name}</h3>
      <span class="score-pill" style="background:${scoreColor(p.investmentPriorityScore)}">${p.investmentPriorityScore}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-metric"><span>Stations</span><strong>${formatNumber(p.stationCount)}</strong></div>
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

function selectRegion(feature, layer = null) {
  selectedRegionId = feature.properties.id;

  if (selectedRegionLayer) {
    regionLayer.resetStyle(selectedRegionLayer);
  }

  if (layer) {
    selectedRegionLayer = layer;
    layer.setStyle(regionStyle(feature));
    layer.bringToFront();
  } else {
    regionLayer.eachLayer((candidate) => {
      if (candidate.feature.properties.id === selectedRegionId) {
        selectedRegionLayer = candidate;
        candidate.setStyle(regionStyle(candidate.feature));
        candidate.bringToFront();
      }
    });
  }

  renderRegionDetail(feature);
}

function updateKpis(stations, regions) {
  const averageDensity = regions.features.reduce((sum, feature) => sum + feature.properties.chargerDensity, 0) / regions.features.length;
  const bestRegion = sortedRegions("evReadinessScore")[0].properties;
  const underservedRegion = sortedRegions("chargerDeficitScore")[0].properties;
  const priorityRegion = sortedRegions("investmentPriorityScore")[0].properties;

  setText("kpi-total-stations", formatNumber(stations.features.length));
  setText("kpi-average-density", `${round(averageDensity, 1)} / 10k`);
  setText("kpi-best-region", bestRegion.name);
  setText("kpi-underserved-region", underservedRegion.name);
  setText("kpi-priority-region", priorityRegion.name);
}

function renderRanking(metric = activeRanking) {
  activeRanking = metric;
  const list = document.getElementById("ranking-list");
  const topRegions = sortedRegions(metric).slice(0, 10);

  document.querySelectorAll(".ranking-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.ranking === metric);
  });

  list.innerHTML = "";
  topRegions.forEach((feature, index) => {
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

function addDashboardData(stations, regions, substations) {
  regionFeatures = regions.features;
  stationFeatures = stations.features;
  selectedRegionId = null;
  selectedRegionLayer = null;

  regionLayer.clearLayers();
  stationLayer.clearLayers();
  substationLayer.clearLayers();
  regionLayer.addData(regions);
  stationLayer.addData(stations);
  substationLayer.addData(substations);

  updateKpis(stations, regions);
  renderRegionDetail(null);
  renderRanking(activeRanking);
  map.fitBounds(regionLayer.getBounds(), { padding: [24, 24] });
}

function useSampleData(note = null) {
  addDashboardData(SAMPLE_STATIONS, SAMPLE_REGIONS, SAMPLE_SUBSTATIONS);

  if (note) {
    document.querySelector(".map-note").textContent = note;
  }
}

async function fetchGeoJson(path) {
  const response = await fetch(path);

  if (!response.ok) {
    throw new Error(`Could not load ${path}`);
  }

  return response.json();
}

function looksLikeGermanyAnalytics(regions, stations) {
  return (
    regions.features?.length >= 20 &&
    stations.features?.length >= 5000 &&
    regions.features.every((feature) => feature.properties.investmentPriorityScore)
  );
}

async function loadGeoJson() {
  const [stations, regions] = await Promise.all([
    fetchGeoJson("data/ev_charging_stations_sample.geojson"),
    fetchGeoJson("data/germany_regions_sample.geojson")
  ]);

  if (!looksLikeGermanyAnalytics(regions, stations)) {
    useSampleData("Using generated Germany intelligence sample data. GeoJSON files can be replaced by GeoServer WMS/WFS services in the full stack.");
    return;
  }

  addDashboardData(stations, regions, buildSubstationsGeoJson(regions));
}

buildDatasetList();

document.getElementById("score-mode").addEventListener("change", (event) => {
  activeScoreMode = event.target.value;
  regionLayer.setStyle(regionStyle);
});

document.querySelectorAll(".ranking-tab").forEach((button) => {
  button.addEventListener("click", () => renderRanking(button.dataset.ranking));
});

loadGeoJson().catch((error) => {
  console.error(error);
  useSampleData("Using built-in Germany energy infrastructure sample data. Run Docker/nginx to serve external GeoJSON files.");
});
