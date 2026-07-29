(function () {
  const NRW_CENTER = [51.43, 7.66];
  const NRW_BOUNDS = [[50.32, 5.86], [52.53, 9.47]];

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

  const runtimeConfig = window.__energyAppConfig || {
    geonodeBaseUrl: "http://localhost:8000",
    geoserverBaseUrl: "http://localhost:8080",
    geonodeStationsLayer: "geonode:nrw_ev_charging_stations",
    geonodeRegionsLayer: "geonode:nrw_nuts3_districts"
  };

  const scoreLabels = {
    investmentPriorityScore: "Investment Priority",
    chargingSupplyScore: "Charging Supply",
    chargerDeficitScore: "Charger Deficit",
    dataQualityScore: "Data Quality"
  };

  const scoreKeys = {
    investmentPriorityScore: ["investmentPriorityScore", "investment_priority_score"],
    chargingSupplyScore: ["chargingSupplyScore", "charging_supply_score"],
    chargerDeficitScore: ["chargerDeficitScore", "charger_deficit_score"],
    dataQualityScore: ["dataQualityScore", "data_quality_score"]
  };

  const fallbackRegions = [
    ["DEA23", "Köln, Kreisfreie Stadt", [6.76, 50.83, 7.17, 51.08], 920, 1460, 138, 782, 92, 8, 35],
    ["DEA11", "Düsseldorf, Kreisfreie Stadt", [6.68, 51.12, 6.94, 51.35], 670, 1040, 102, 568, 84, 16, 35],
    ["DEA52", "Dortmund, Kreisfreie Stadt", [7.31, 51.39, 7.64, 51.62], 430, 720, 72, 358, 67, 33, 35],
    ["DEA13", "Essen, Kreisfreie Stadt", [6.86, 51.33, 7.14, 51.55], 360, 580, 61, 299, 58, 42, 35],
    ["DEA33", "Münster, Kreisfreie Stadt", [7.47, 51.84, 7.78, 52.07], 420, 690, 75, 345, 66, 34, 35],
    ["DEA21", "Aachen, Städteregion", [5.96, 50.56, 6.52, 50.98], 390, 640, 68, 322, 60, 40, 35],
    ["DEA41", "Bielefeld, Kreisfreie Stadt", [8.38, 51.90, 8.70, 52.12], 310, 510, 47, 263, 51, 49, 35],
    ["DEA22", "Bonn, Kreisfreie Stadt", [7.02, 50.62, 7.23, 50.79], 330, 530, 56, 274, 55, 45, 35]
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

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    })[character]);
  }

  function firstValue(properties, keys) {
    return keys.map((key) => properties[key]).find((value) => value !== undefined && value !== null && value !== "");
  }

  function textValue(properties, keys, fallback = "-") {
    const value = firstValue(properties, keys);
    return value === undefined ? fallback : String(value);
  }

  function numericValue(properties, keys, fallback = 0) {
    const value = firstValue(properties, keys);
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function regionName(properties) {
    return textValue(properties, ["district_name", "name", "region_name"], "NRW district");
  }

  function regionId(properties) {
    return textValue(properties, ["id", "nuts_code", "district_code", "name"], regionName(properties));
  }

  function scoreValue(properties, metric) {
    return numericValue(properties, scoreKeys[metric], 0);
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
  }

  function formatScore(value) {
    return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }

  function trimTrailingSlash(value) {
    return String(value).replace(/\/+$/, "");
  }

  function geoserverWfsUrl(typeName) {
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
      features: fallbackRegions.map(([nutsCode, name, box, stationCount, chargingPoints, fastChargers, normalChargers, supply, deficit, quality], index) => ({
        type: "Feature",
        properties: {
          id: nutsCode,
          nuts_code: nutsCode,
          district_name: name,
          name,
          region: "Nordrhein-Westfalen",
          region_abbr: "NRW",
          box,
          stationCount,
          chargers_total: stationCount,
          charging_points_total: chargingPoints,
          fast_chargers: fastChargers,
          normal_chargers: normalChargers,
          chargingSupplyScore: supply,
          charging_supply_score: supply,
          chargerDeficitScore: deficit,
          charger_deficit_score: deficit,
          investmentPriorityScore: 100 - supply,
          investment_priority_score: 100 - supply,
          priorityRank: index + 1,
          priority_rank: index + 1,
          priorityTier: deficit > 45 ? "high" : "screening",
          priority_tier: deficit > 45 ? "high" : "screening",
          dataQualityScore: quality,
          data_quality_score: quality,
          dataQualityFlag: "direct_html_preview"
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
        return Array.from({ length: 36 }, (_, localIndex) => {
          const col = localIndex % 9;
          const row = Math.floor(localIndex / 9);
          const longitude = west + ((col + 0.5) / 9) * (east - west);
          const latitude = south + ((row + 0.5) / 4) * (north - south);
          const id = `nrw-preview-${index++}`;
          const power = [22, 50, 150, 300][localIndex % 4];
          return {
            type: "Feature",
            properties: {
              id,
              name: `${region.properties.name} preview station ${localIndex + 1}`,
              operator: ["BNetzA preview", "Aral pulse", "EnBW", "Allego"][localIndex % 4],
              district_name: region.properties.name,
              region: "Nordrhein-Westfalen",
              state: "Nordrhein-Westfalen",
              power_kw: power,
              charging_points: 2 + (localIndex % 4),
              status: "preview"
            },
            geometry: { type: "Point", coordinates: [longitude, latitude] }
          };
        });
      })
    };
  }

  async function loadJson(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  async function loadJsonWithFallback(primaryPath, fallbackPath) {
    if (primaryPath === fallbackPath) {
      return { data: await loadJson(primaryPath), source: `Local GeoJSON: ${fallbackPath}` };
    }

    try {
      return { data: await loadJson(primaryPath), source: `GeoNode WFS: ${primaryPath}` };
    } catch (error) {
      console.warn(`Falling back from ${primaryPath} to ${fallbackPath}`, error);
      return { data: await loadJson(fallbackPath), source: `Local GeoJSON fallback: ${fallbackPath}` };
    }
  }

  function renderDatasets() {
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

  function renderMetadata(datasetId) {
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

  function sortedRegions(metric) {
    return [...regionFeatures].sort((a, b) => scoreValue(b.properties, metric) - scoreValue(a.properties, metric));
  }

  function regionStyle(feature) {
    const score = scoreValue(feature.properties, activeScore);
    return {
      color: selectedRegionId === regionId(feature.properties) ? "#74f0d2" : "#213244",
      weight: selectedRegionId === regionId(feature.properties) ? 3 : 1,
      fillColor: scoreColor(score),
      fillOpacity: 0.6
    };
  }

  function renderKpis(stations, regions) {
    const supplyScores = regions.features.map((feature) => scoreValue(feature.properties, "chargingSupplyScore"));
    const averageSupply = supplyScores.reduce((sum, score) => sum + score, 0) / Math.max(1, supplyScores.length);
    setText("kpi-total-stations", new Intl.NumberFormat("en-US").format(stations.features.length));
    setText("kpi-average-density", `${averageSupply.toFixed(0)} / 100`);
    setText("kpi-best-region", regionName(sortedRegions("chargingSupplyScore")[0].properties));
    setText("kpi-underserved-region", regionName(sortedRegions("chargerDeficitScore")[0].properties));
    setText("kpi-priority-region", regionName(sortedRegions("investmentPriorityScore")[0].properties));
  }

  function renderRegionDetail(feature) {
    const detail = document.getElementById("region-detail");
    if (!detail) return;
    if (!feature) {
      detail.innerHTML = '<p class="empty-state">Select a NRW district to inspect charging supply, deficit, priority rank and data gaps.</p>';
      return;
    }
    const p = feature.properties;
    const investment = scoreValue(p, "investmentPriorityScore");
    const supply = scoreValue(p, "chargingSupplyScore");
    const deficit = scoreValue(p, "chargerDeficitScore");
    const quality = scoreValue(p, "dataQualityScore");
    const priorityRank = numericValue(p, ["priorityRank", "priority_rank"], sortedRegions("investmentPriorityScore").findIndex((item) => regionId(item.properties) === regionId(p)) + 1);
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
        <div class="detail-metric"><span>Priority rank</span><strong>#${formatNumber(priorityRank)}</strong></div>
        <div class="detail-metric"><span>Data quality</span><strong>${formatScore(quality)}</strong></div>
      </div>
      <p class="recommendation">Current POC priority is charger-supply based. Add population, road and grid layers before a final investment decision.</p>
    `;
  }

  function selectRegion(feature, layer) {
    selectedRegionId = regionId(feature.properties);
    if (selectedLayer) regionLayer.resetStyle(selectedLayer);
    selectedLayer = layer || null;
    if (!selectedLayer) {
      regionLayer.eachLayer((candidate) => {
        if (candidate.feature && regionId(candidate.feature.properties) === selectedRegionId) selectedLayer = candidate;
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
        if (p.box) map.fitBounds(boundsFromBox(p.box), { padding: [24, 24], maxZoom: 9 });
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
      const regionsResult = await loadJsonWithFallback(geoserverWfsUrl(runtimeConfig.geonodeRegionsLayer), "data/nrw_regions_sample.geojson");
      const stationsResult = await loadJsonWithFallback(geoserverWfsUrl(runtimeConfig.geonodeStationsLayer), "data/nrw_charging_stations_sample.geojson");
      regions = regionsResult.data;
      stations = stationsResult.data;
      document.querySelector(".map-note").textContent = regionsResult.source.indexOf("GeoNode") === 0 || stationsResult.source.indexOf("GeoNode") === 0 ? "GeoNode-connected preview: regions and/or charging stations are being served from GeoServer WFS, with local GeoJSON fallback if the stack is offline." : "Local-first preview: GeoNode is offline, so the dashboard is using the checked-in NRW GeoJSON snapshot.";
    } catch (error) {
      regions = fallbackRegionCollection();
      stations = fallbackStations(regions);
      document.querySelector(".map-note").textContent = "Direct HTML preview is using a small embedded NRW subset. Start a local web server for the full 21k+ BNetzA charger records.";
    }

    regionFeatures = regions.features;
    map = L.map("map", { preferCanvas: true, zoomControl: true }).setView(NRW_CENTER, 7);
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
        layer.bindTooltip(`${regionName(feature.properties)}: ${formatScore(scoreValue(feature.properties, activeScore))} (${scoreLabels[activeScore]})`, { sticky: true });
      }
    }).addTo(map);

    const stationLayer = L.geoJSON(stations, {
      pane: "stations",
      pointToLayer: (feature, latlng) => {
        const power = numericValue(feature.properties || {}, ["power_kw"], 22);
        return L.circleMarker(latlng, {
          radius: stations.features.length > 10000 ? 1.6 : 2.8,
          color: power >= 150 ? "#f97316" : "#0ea5e9",
          weight: 0.5,
          fillColor: power >= 150 ? "#fb923c" : "#38bdf8",
          fillOpacity: 0.72
        });
      },
      onEachFeature: (feature, layer) => {
        const p = feature.properties || {};
        layer.bindPopup(`
          <div class="station-popup">
            <h3>${escapeHtml(textValue(p, ["name"], "Charging station"))}</h3>
            <p><strong>Operator:</strong> ${escapeHtml(textValue(p, ["operator"], "unknown"))}</p>
            <p><strong>District:</strong> ${escapeHtml(textValue(p, ["district_name", "region"], "NRW"))}</p>
            <p><strong>Power:</strong> ${formatNumber(numericValue(p, ["power_kw"], 0))} kW</p>
            <p><strong>Charging points:</strong> ${formatNumber(numericValue(p, ["charging_points", "connectors"], 0))}</p>
          </div>
        `);
      }
    }).addTo(map);

    L.control.layers({ "Carto Dark": carto, OpenStreetMap: osm }, {
      "EV charging stations": stationLayer,
      "NRW NUTS-3 districts": regionLayer
    }, { collapsed: false }).addTo(map);

    renderKpis(stations, regions);
    renderRanking();
    map.fitBounds(regionLayer.getBounds().isValid() ? regionLayer.getBounds() : NRW_BOUNDS, { padding: [24, 24] });

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
