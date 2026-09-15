import { describe, expect, it } from "vitest";
import html from "../index.html?raw";
import nginx from "../nginx.conf?raw";
import mainSource from "./main.ts?raw";


describe("dashboard information architecture", () => {
  it("contains no inert navigation, shortcut, trend, or decorative source controls", () => {
    for (const removed of [
      "nav-rail",
      "tool-tabs",
      ">PTR<",
      ">SEL<",
      "sparkline",
      "donut-panel",
      "bottom-strip",
      "Scenario Trend"
    ]) {
      expect(html).not.toContain(removed);
    }
  });

  it("keeps the working proposed-station action", () => {
    expect(html).toContain('id="scenario-add-button"');
    expect(html).toContain('id="map-add-station"');
    expect(html).toContain("Add proposed station");
    expect(html).toContain('id="scenario-max-point-power"');
    expect(html).toContain('id="scenario-latitude"');
    expect(html).toContain('id="scenario-longitude"');
    expect(html).toContain("Maximum point power");
    expect(html).toContain("Clear this browser's proposals");
  });

  it("ships no direct-HTML preview or synthetic fallback path", () => {
    expect(html).not.toContain("preview.js");
    expect(html).not.toContain("bootStaticPreview");
    expect(mainSource).not.toContain("nrw-preview-");
    expect(mainSource).not.toContain("chargingSupplyScore");
    expect(mainSource).not.toContain("priorityTier");
  });

  it("uses canonical WFS layers and keeps scenario failures gated", () => {
    for (const layer of [
      "nrw:nrw_ev_baseline_metrics",
      "nrw:nrw_chargers",
      "nrw:nrw_ev_scenario_metrics",
      "nrw:nrw_renewable_potential",
      "nrw:nrw_regional_roads",
      "nrw:nrw_autobahns"
    ]) expect(mainSource).toContain(layer);
    expect(mainSource).toContain('srsName: "EPSG:4326"');
    expect(mainSource).toContain("setScenarioAvailability");
    expect(mainSource).toContain("not ranked");
    expect(mainSource).not.toContain("geonode:nrw_");
  });

  it("provides local fallbacks for both road overlays", () => {
    expect(mainSource).toContain("data/nrw_autobahns_sample.geojson");
    expect(mainSource).toContain("data/nrw_regional_roads_sample.geojson");
    expect(mainSource).toContain('"Autobahns": autobahnLayer');
    expect(mainSource).toContain('"Federal and state roads": regionalRoadLayer');
  });

  it("serves canonical GeoJSON fallbacks with a JSON media type", () => {
    expect(nginx).toContain("include /etc/nginx/mime.types");
    expect(nginx).toContain("application/geo+json geojson");
  });

  it("re-resolves the GeoServer upstream instead of caching it at startup", () => {
    // A literal host in proxy_pass is resolved once, at boot: nginx then refuses to
    // start while GeoServer is absent, and answers 502 from a stale address after
    // GeoServer is recreated on a new IP.
    expect(nginx).toContain("resolver 127.0.0.11");
    expect(nginx).toContain("set $geoserver_upstream http://geoserver:8080;");
    expect(nginx).toContain("proxy_pass $geoserver_upstream$request_uri;");
    expect(nginx).not.toContain("proxy_pass http://geoserver:8080/geoserver/;");
  });

  it("provides a renewable-energy overlay with a local fallback", () => {
    expect(mainSource).toContain('renewableAssetsLayer: "nrw:nrw_renewable_potential"');
    expect(mainSource).toContain("data/nrw_renewable_assets_sample.geojson");
    expect(mainSource).toContain('"Solar farms and wind energy": renewableAssetLayer');
    expect(mainSource).toContain('}).addTo(map);');
    expect(mainSource).toContain("Installed capacity:");
    expect(mainSource).toContain("<strong>Operator:</strong>");
  });

  it("keeps a working catalogue listing route and observable basemap fallback", () => {
    expect(html).toContain('href="http://localhost:8000/datasets"');
    expect(mainSource).toContain('"Vector-only fallback": vectorFallback');
    expect(mainSource).toContain('osm.on("tileerror", activateBasemapFallback)');
    expect(mainSource).toContain("event.detail === 0");
  });

  it("explains every composite indicator and its weights", () => {
    expect(html).toContain("40% Charging-point Density");
    expect(html).toContain("30% Charger Accessibility");
    expect(html).toContain("30% Population-adjusted Coverage");
    expect(html).toContain("Charger Deficit = 100 − EV Readiness");
    expect(html).toContain("40% Transport Load");
    expect(html).toContain("35% Grid Readiness Proxy");
    expect(html).toContain("25% Renewable Context");
    expect(html).toContain("60% Charger Deficit");
    expect(html).toContain("40% Infrastructure Opportunity");
    expect(html).toContain("45% Local Energy Balance");
    expect(html).toContain("30% Renewable Growth");
  });

  it("distinguishes charging-point supply from station locations in the EV model", () => {
    expect(html).toContain("Density measures charging points per km²");
    expect(html).toContain("nearest station: closer scores higher");
    expect(html).toContain("charging points per 100,000 residents");
  });

  it("makes change-view KPI and district labels explicit about score changes", () => {
    for (const id of [
      "kpi-total-stations-label",
      "kpi-average-readiness-label",
      "kpi-best-readiness-label",
      "kpi-underserved-label",
      "kpi-priority-label"
    ]) {
      expect(html).toContain(`id="${id}"`);
    }
    expect(mainSource).toContain("Highest charging-gap change");
    expect(mainSource).toContain("Highest signed change; increases, then unchanged values, then decreases.");
    expect(mainSource).toContain("This is a change view, not a site recommendation.");
    expect(mainSource).toContain("Scenario priority rank");
    expect(html).toContain('id="kpi-total-stations-note"');
  });

  it("lists project data sources as text", () => {
    for (const source of [
      "Bundesnetzagentur",
      "Eurostat GISCO",
      "Eurostat population",
      "Straßen.NRW",
      "Open Power System Data",
      "Energieatlas NRW",
      "OpenStreetMap / Geofabrik"
    ]) {
      expect(html).toContain(source);
    }
    expect(html).toContain("© EuroGeographics for the administrative boundaries.");
    expect(html).toContain("data/runtime/current/manifest.json");
    expect(mainSource).toContain("© EuroGeographics for the administrative boundaries");
    expect(mainSource).toContain("map.attributionControl.addAttribution");
  });
});
