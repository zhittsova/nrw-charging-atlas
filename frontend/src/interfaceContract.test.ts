import { describe, expect, it } from "vitest";
import html from "../index.html?raw";


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
