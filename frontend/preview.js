(function () {
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

  const fallbackRegions = [
    ["de-berlin", "Berlin", "Berlin", [13.08, 52.35, 13.76, 52.68], 2714, 7.2, 1.2, 84, 79, 54, 83.9],
    ["de-hamburg", "Hamburg", "Hamburg", [9.7, 53.39, 10.33, 53.74], 1291, 6.8, 1.3, 82, 77, 57, 80.5],
    ["de-munich", "Munich Region", "Bavaria", [11.05, 47.82, 12.05, 48.45], 2503, 8.4, 3.1, 79, 83, 49, 78.2],
    ["de-frankfurt", "Frankfurt Rhine-Main", "Hesse", [7.85, 49.65, 9.15, 50.45], 3544, 6.1, 4.8, 86, 68, 63, 82.1],
    ["de-ruhr", "Ruhr Area", "North Rhine-Westphalia", [6.55, 51.18, 7.65, 51.75], 2397, 4.7, 3.9, 83, 51, 78, 81.3],
    ["de-stuttgart", "Stuttgart Region", "Baden-Wurttemberg", [8.75, 48.45, 9.65, 49.05], 2184, 7.8, 2.4, 81, 80, 46, 77.8],
    ["de-cologne", "Cologne-Bonn Region", "North Rhine-Westphalia", [6.55, 50.55, 7.45, 51.15], 1851, 5.2, 4.3, 76, 56, 72, 79.4],
    ["de-dresden", "Dresden Region", "Saxony", [12.8, 50.75, 14.1, 51.25], 670, 5.0, 6.5, 71, 50, 61, 63.5],
    ["de-leipzig", "Leipzig Region", "Saxony", [11.85, 51.05, 12.75, 51.55], 504, 4.8, 6.2, 69, 49, 65, 65.1],
    ["de-hannover", "Hannover Region", "Lower Saxony", [9.4, 52.15, 10.05, 52.6], 626, 5.4, 4.7, 74, 55, 59, 66.6],
    ["de-bremen", "Bremen-Oldenburg", "Bremen / Lower Saxony", [8.3, 52.85, 9.15, 53.45], 739, 4.2, 7.2, 70, 42, 70, 67.2],
    ["de-kiel", "Kiel-Schleswig", "Schleswig-Holstein", [9.55, 53.95, 10.7, 54.65], 437, 4.5, 7.6, 65, 41, 69, 55.4],
    ["de-potsdam", "Potsdam-Havelland", "Brandenburg", [12.55, 52.2, 13.25, 52.65], 312, 4.1, 7.1, 68, 38, 71, 57.6],
    ["de-nuremberg", "Nuremberg Region", "Bavaria", [10.65, 49.1, 11.45, 49.75], 870, 6.4, 5.6, 74, 58, 54, 65.8],
    ["de-freiburg", "Freiburg-South Baden", "Baden-Wurttemberg", [7.45, 47.62, 8.35, 48.25], 759, 6.6, 5.2, 67, 64, 42, 55.9],
    ["de-cottbus", "Lausitz-Cottbus", "Brandenburg / Saxony", [13.85, 51.25, 14.75, 51.95], 203, 2.9, 12.5, 62, 25, 80, 54.8]
  ];

  let activeScore = "investmentPriorityScore";
  let activeRanking = "investmentPriorityScore";
  let regionFeatures = [];
  let selectedRegionId = null;
  let selectedLayer = null;
  let map = null;
  let regionLayer = null;

  function polygonFromBox(box) {
    const [west, south, east, north] = box;
    return [[[west, south], [east, south], [east, north], [west, north], [west, south]]];
  }

  function boundsFromBox(box) {
    const [west, south, east, north] = box;
    return [[south, west], [north, east]];
  }

  function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
  }

  function scoreColor(score) {
    if (score >= 80) return "#0f766e";
    if (score >= 65) return "#4d9f75";
    if (score >= 50) return "#c0a33c";
    if (score >= 35) return "#d97706";
    return "#b91c1c";
  }

  function fallbackRegionCollection() {
    return {
      type: "FeatureCollection",
      features: fallbackRegions.map(([id, name, state, box, stationCount, chargerDensity, averageDistanceKm, gridReadinessScore, evReadinessScore, chargerDeficitScore, investmentPriorityScore]) => ({
        type: "Feature",
        properties: {
          id,
          name,
          state,
          box,
          stationCount,
          chargerDensity,
          averageDistanceKm,
          substationCount: Math.max(3, Math.round(gridReadinessScore / 7)),
          evReadinessScore,
          gridReadinessScore,
          chargerDeficitScore,
          investmentPriorityScore
        },
        geometry: { type: "Polygon", coordinates: polygonFromBox(box) }
      }))
    };
  }

  function fallbackStations(regions) {
    let index = 1;
    return {
      type: "FeatureCollection",
      features: regions.features.flatMap((region) => {
        const [west, south, east, north] = region.properties.box;
        return Array.from({ length: 24 }, (_, localIndex) => {
          const col = localIndex % 6;
          const row = Math.floor(localIndex / 6);
          const longitude = west + ((col + 0.5) / 6) * (east - west);
          const latitude = south + ((row + 0.5) / 4) * (north - south);
          const id = `preview-${index++}`;
          return {
            type: "Feature",
            properties: {
              id,
              name: `${region.properties.name} charging preview ${localIndex + 1}`,
              operator: ["EnBW", "Aral pulse", "Ionity", "Allego"][localIndex % 4],
              region: region.properties.name,
              power_kw: [22, 50, 150, 300][localIndex % 4],
              connectors: 2 + (localIndex % 5),
              status: localIndex % 5 === 0 ? "occupied" : "available"
            },
            geometry: { type: "Point", coordinates: [longitude, latitude] }
          };
        });
      })
    };
  }

  async function loadJson(path) {
    if (location.protocol === "file:") throw new Error("direct file preview");
    const response = await fetch(path);
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  function renderDatasets() {
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

  function renderMetadata(datasetId) {
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

  function sortedRegions(metric) {
    return [...regionFeatures].sort((a, b) => b.properties[metric] - a.properties[metric]);
  }

  function regionStyle(feature) {
    const score = feature.properties[activeScore];
    return {
      color: selectedRegionId === feature.properties.id ? "#0b5f59" : "#334155",
      weight: selectedRegionId === feature.properties.id ? 3 : 1.1,
      fillColor: scoreColor(score),
      fillOpacity: 0.55
    };
  }

  function renderKpis(stations, regions) {
    const averageDensity = regions.features.reduce((sum, feature) => sum + Number(feature.properties.chargerDensity || 0), 0) / regions.features.length;
    const totalStations = stations.features.length > 500 ? stations.features.length : regions.features.reduce((sum, feature) => sum + Number(feature.properties.stationCount || 0), 0);
    setText("kpi-total-stations", new Intl.NumberFormat("en-US").format(totalStations));
    setText("kpi-average-density", `${averageDensity.toFixed(1)} / 10k`);
    setText("kpi-best-region", sortedRegions("evReadinessScore")[0].properties.name);
    setText("kpi-underserved-region", sortedRegions("chargerDeficitScore")[0].properties.name);
    setText("kpi-priority-region", sortedRegions("investmentPriorityScore")[0].properties.name);
  }

  function renderRegionDetail(feature) {
    const detail = document.getElementById("region-detail");
    if (!detail) return;
    if (!feature) {
      detail.innerHTML = '<p class="empty-state">Select a region to inspect charger supply, grid readiness, demand, and priority.</p>';
      return;
    }
    const p = feature.properties;
    const priorityRank = sortedRegions("investmentPriorityScore").findIndex((item) => item.properties.id === p.id) + 1;
    detail.innerHTML = `
      <div class="detail-title">
        <h3>${p.name}</h3>
        <span class="score-pill" style="background:${scoreColor(p.investmentPriorityScore)}">${p.investmentPriorityScore}</span>
      </div>
      <div class="detail-grid">
        <div class="detail-metric"><span>Stations</span><strong>${Number(p.stationCount || 0).toLocaleString("en-US")}</strong></div>
        <div class="detail-metric"><span>Per 10k residents</span><strong>${p.chargerDensity}</strong></div>
        <div class="detail-metric"><span>EV readiness</span><strong>${p.evReadinessScore}</strong></div>
        <div class="detail-metric"><span>Grid readiness</span><strong>${p.gridReadinessScore}</strong></div>
        <div class="detail-metric"><span>Priority score</span><strong>${p.investmentPriorityScore}</strong></div>
        <div class="detail-metric"><span>Priority rank</span><strong>#${priorityRank}</strong></div>
        <div class="detail-metric"><span>Avg station distance</span><strong>${p.averageDistanceKm} km</strong></div>
        <div class="detail-metric"><span>Substations</span><strong>${p.substationCount}</strong></div>
      </div>
      <p class="recommendation">Preview mode. For full 23k stations and local files, run a local web server or Vite.</p>
    `;
  }

  function selectRegion(feature, layer) {
    selectedRegionId = feature.properties.id;
    if (selectedLayer) regionLayer.resetStyle(selectedLayer);
    selectedLayer = layer || null;
    if (!selectedLayer) {
      regionLayer.eachLayer((candidate) => {
        if (candidate.feature && candidate.feature.properties.id === selectedRegionId) selectedLayer = candidate;
      });
    }
    if (selectedLayer) selectedLayer.setStyle(regionStyle(feature));
    renderRegionDetail(feature);
  }

  function renderRanking(metric = activeRanking) {
    activeRanking = metric;
    const list = document.getElementById("ranking-list");
    if (!list) return;
    document.querySelectorAll(".ranking-tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.ranking === metric);
    });
    list.innerHTML = "";
    sortedRegions(metric).slice(0, 10).forEach((feature, index) => {
      const p = feature.properties;
      const width = Math.max(8, Math.min(100, p[metric]));
      const item = document.createElement("li");
      item.className = "ranking-item";
      item.innerHTML = `
        <span class="ranking-rank">#${index + 1}</span>
        <span class="ranking-dot"></span>
        <span class="ranking-name" title="${p.name}">${p.name}</span>
        <span class="ranking-bar"><i style="width:${width}%"></i></span>
        <span class="score-pill" style="background:${scoreColor(p[metric])}">${p[metric]}</span>
      `;
      item.addEventListener("click", () => {
        selectRegion(feature);
        map.fitBounds(boundsFromBox(p.box), { padding: [24, 24], maxZoom: 8 });
      });
      list.appendChild(item);
    });
  }

  async function bootStaticPreview() {
    renderDatasets();
    renderRegionDetail(null);

    if (!window.L) {
      document.querySelector(".map-note").textContent = "Leaflet could not be loaded. Check internet connection or run the Vite frontend.";
      return;
    }

    let regions;
    let stations;
    try {
      regions = await loadJson("data/germany_regions_sample.geojson");
      stations = await loadJson("data/ev_charging_stations_sample.geojson");
    } catch (error) {
      regions = fallbackRegionCollection();
      stations = fallbackStations(regions);
      document.querySelector(".map-note").textContent = "Direct HTML preview is using a smaller embedded dataset. Run a local server for the full GeoJSON files.";
    }

    regionFeatures = regions.features;
    map = L.map("map", { preferCanvas: true, zoomControl: true }).setView([51.1, 10.2], 6);
    map.createPane("regions");
    map.createPane("stations");
    map.getPane("regions").style.zIndex = "410";
    map.getPane("stations").style.zIndex = "460";

    const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }).addTo(map);
    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    });

    regionLayer = L.geoJSON(regions, {
      pane: "regions",
      style: regionStyle,
      onEachFeature: (feature, layer) => {
        layer.on("click", () => selectRegion(feature, layer));
        layer.bindTooltip(`${feature.properties.name}: ${feature.properties[activeScore]}`, { sticky: true });
      }
    }).addTo(map);

    const stationLayer = L.geoJSON(stations, {
      pane: "stations",
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: stations.features.length > 1000 ? 2.2 : 3.4,
        color: "#8a2b09",
        weight: 0.5,
        fillColor: "#d9480f",
        fillOpacity: 0.65
      })
    }).addTo(map);

    L.control.layers({ "Carto Dark": carto, OpenStreetMap: osm }, {
      "EV charging stations": stationLayer,
      "Administrative regions": regionLayer
    }, { collapsed: false }).addTo(map);

    renderKpis(stations, regions);
    renderRanking();
    map.fitBounds(regionLayer.getBounds(), { padding: [24, 24] });

    document.getElementById("score-mode").addEventListener("change", (event) => {
      activeScore = event.target.value;
      regionLayer.setStyle(regionStyle);
    });
    document.querySelectorAll(".ranking-tab").forEach((button) => {
      button.addEventListener("click", () => renderRanking(button.dataset.ranking));
    });
  }

  window.bootStaticPreview = bootStaticPreview;
})();
