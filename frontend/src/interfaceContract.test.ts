import { describe, expect, it } from "vitest";
import html from "../index.html?raw";
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
  });

  it("provides local fallbacks for both road overlays", () => {
    expect(mainSource).toContain("data/nrw_autobahns_sample.geojson");
    expect(mainSource).toContain("data/nrw_regional_roads_sample.geojson");
    expect(mainSource).toContain('"Autobahns": autobahnLayer');
    expect(mainSource).toContain('"Federal and state roads": regionalRoadLayer');
  });

  it("provides a renewable-energy overlay with a local fallback", () => {
    expect(html).toContain('renewableAssetsLayer: "nrw:nrw_renewable_potential"');
    expect(mainSource).toContain("data/nrw_renewable_assets_sample.geojson");
    expect(mainSource).toContain('"Solar farms and wind energy": renewableAssetLayer');
    expect(mainSource).toContain("Installed capacity:");
    expect(mainSource).toContain("<strong>Operator:</strong>");
  });

  it("explains every composite indicator and its weights", () => {
    expect(html).toContain("40% Charger Density");
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
    expect(html).toContain("data/raw/provenance/");
  });
});
