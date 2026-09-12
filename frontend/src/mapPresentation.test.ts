import { describe, expect, it } from "vitest";
import {
  AUTOBAHN_STYLE,
  illuminatedScoreColor,
  OVERLAY_ORDER,
  REGIONAL_ROAD_STYLE,
  RENEWABLE_LEGEND_ITEMS,
  RENEWABLE_TECHNOLOGY_COLORS,
  isDisplayedRenewableTechnology,
  officialStationStyle,
  operatorTooltip,
  renewableAssetStyle,
  renewableTechnologyLabel,
  selectAutobahnFeaturesForZoom,
  selectOfficialStationsForZoom,
  selectRenewableFeaturesForZoom,
  scoreColor
} from "./mapPresentation";

describe("map presentation", () => {
  it("uses a continuous blue-to-purple score ramp with clamped endpoints", () => {
    expect([0, 25, 50, 75, 100].map((score) => scoreColor(score, "baseline", "evReadinessScore")))
      .toEqual(["#f2f7ff", "#b8d9ff", "#4399f4", "#5935c2", "#470c75"]);
    expect(scoreColor(12.5, "scenario", "investmentPriorityScore")).toBe("#d5e8ff");
    expect(scoreColor(-1, "baseline", "evReadinessScore")).toBe("#f2f7ff");
    expect(scoreColor(101, "baseline", "evReadinessScore")).toBe("#470c75");
  });

  it("keeps illuminated bins increasing in lightness and preserves change semantics", () => {
    const luminance = (hex: string) => {
      const channels = [1, 3, 5].map((start) => {
        const value = parseInt(hex.slice(start, start + 2), 16) / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    };
    const values = [0, 35, 50, 65, 80].map((score) =>
      luminance(illuminatedScoreColor(score, "baseline", "investmentPriorityScore")));
    values.slice(1).forEach((value, index) => expect(value).toBeGreaterThan(values[index]));
    for (const metric of ["evReadinessScore", "chargerDeficitScore"] as const) {
      for (const delta of [-12, -5, 0, 5, 12]) {
        expect(illuminatedScoreColor(delta, "change", metric)).toBe(scoreColor(delta, "change", metric));
      }
    }
  });

  it("interpolates the dark-blue to yellow ramp and clamps score bounds", () => {
    expect(illuminatedScoreColor(0, "baseline", "investmentPriorityScore")).toBe("#081a3b");
    expect(illuminatedScoreColor(100, "baseline", "investmentPriorityScore")).toBe("#fff36b");
    expect(illuminatedScoreColor(90, "scenario", "investmentPriorityScore")).toBe("#ffe34f");
    expect(illuminatedScoreColor(-1, "baseline", "evReadinessScore")).toBe("#081a3b");
    expect(illuminatedScoreColor(101, "baseline", "evReadinessScore")).toBe("#fff36b");
    expect(illuminatedScoreColor(80, "change", "investmentPriorityScore"))
      .toBe(scoreColor(80, "change", "investmentPriorityScore"));
  });

  it("uses blue-neutral-magenta semantics for scenario change", () => {
    expect(scoreColor(12, "change", "evReadinessScore")).toBe("#2563eb");
    expect(scoreColor(0, "change", "evReadinessScore")).toBe("#64748b");
    expect(scoreColor(-12, "change", "evReadinessScore")).toBe("#be123c");
    expect(scoreColor(-12, "change", "chargerDeficitScore")).toBe("#2563eb");
  });

  it("keeps dense official stations pink without obscuring the district map", () => {
    const normal = officialStationStyle(22);
    const highPower = officialStationStyle(150);
    const statewideNormal = officialStationStyle(22, 7);
    const statewideHighPower = officialStationStyle(150, 7);

    expect(normal.pane).toBe("stations");
    expect(normal.radius).toBeGreaterThanOrEqual(2.7);
    expect(normal.radius).toBeLessThanOrEqual(3.2);
    expect(normal.fillOpacity).toBeGreaterThanOrEqual(0.9);
    expect(normal.fillColor).toBe("#ec4899");
    expect(highPower.radius).toBeGreaterThan(normal.radius);
    expect(highPower.fillColor).toBe("#f472b6");
    expect(highPower.radius).toBeLessThanOrEqual(4.2);
    expect(highPower.weight).toBeGreaterThanOrEqual(0.8);
    expect(statewideNormal.radius).toBeLessThan(2);
    expect(statewideHighPower.radius).toBeLessThan(2.5);
    expect(statewideNormal.fillOpacity).toBeGreaterThanOrEqual(0.85);
  });

  it("escapes operator names and supplies an explicit fallback", () => {
    expect(operatorTooltip("R&D <Charge>"))
      .toBe("<strong>R&amp;D &lt;Charge&gt;</strong><span>Charging-station operator</span>");
    expect(operatorTooltip(undefined)).toContain("Operator not specified");
  });

  it("keeps districts under roads and charging stations", () => {
    expect(OVERLAY_ORDER).toEqual([
      "districts",
      "regionalRoads",
      "autobahns",
      "renewableAssets",
      "officialStations",
      "proposedStations"
    ]);
    expect(AUTOBAHN_STYLE.weight).toBeGreaterThan(REGIONAL_ROAD_STYLE.weight);
    expect(AUTOBAHN_STYLE.weight).toBeLessThanOrEqual(2.2);
  });

  it("encodes renewable technology with color and capacity with marker size", () => {
    expect(RENEWABLE_TECHNOLOGY_COLORS.Windenergie).toBe("#38bdf8");
    expect(RENEWABLE_TECHNOLOGY_COLORS["Photovoltaik Freifläche"]).toBe("#b77900");
    expect(RENEWABLE_LEGEND_ITEMS).toHaveLength(2);
    expect(renewableTechnologyLabel("Photovoltaik Bauliche")).toBe("Rooftop solar");
    expect(renewableAssetStyle("Windenergie", 50).radius)
      .toBeGreaterThan(renewableAssetStyle("Windenergie", 0.1).radius);
    expect(renewableAssetStyle("Windenergie", 2).fillColor).toBe("#38bdf8");
    expect(isDisplayedRenewableTechnology("Windenergie")).toBe(true);
    expect(isDisplayedRenewableTechnology("Photovoltaik Freifläche")).toBe(true);
    expect(isDisplayedRenewableTechnology("Biomasse")).toBe(false);
    expect(isDisplayedRenewableTechnology("Photovoltaik Bauliche")).toBe(false);
    expect(isDisplayedRenewableTechnology("Photovoltaik")).toBe(false);
    expect(isDisplayedRenewableTechnology(undefined)).toBe(false);
  });

  it("keeps the strongest renewable asset per technology group and area", () => {
    const asset = (id: string, technology: string, capacity: number) => ({
      type: "Feature" as const,
      properties: { source_id: id, technology, capacity_mw: capacity },
      geometry: { type: "Point" as const, coordinates: [7.01, 51.01] }
    });
    const rooftop = asset("roof", "Photovoltaik Bauliche", 0.02);
    const solarPark = asset("park", "Photovoltaik Freifläche", 8);
    const wind = asset("wind", "Windenergie", 3);
    const assets = [rooftop, solarPark, wind];

    expect(selectRenewableFeaturesForZoom(assets, 7)).toEqual([solarPark, wind]);
    expect(selectRenewableFeaturesForZoom(assets, 12)).toEqual(assets);
  });

  it("keeps complete longest Autobahn routes at wider views", () => {
    const route = (ref: string, length: number, segment = 0) => ({
      type: "Feature" as const,
      properties: { ref, source_id: `${ref}-${segment}` },
      geometry: {
        type: "LineString" as const,
        coordinates: [[7, 51 + segment * 0.01], [7 + length, 51 + segment * 0.01]]
      }
    });
    const routes = [
      route("A 1", 1, 1),
      route("A 1", 1, 2),
      route("A 2", 1.8),
      route("A 3", 1.6),
      route("A 4", 1.4),
      route("A 5", 0.2)
    ];

    const statewide = selectAutobahnFeaturesForZoom(routes, 6);
    expect(statewide.map((feature) => feature.properties.ref)).toEqual(["A 1", "A 1", "A 2", "A 3", "A 4"]);
    expect(selectAutobahnFeaturesForZoom(routes, 9)).toEqual(routes);
  });

  it("keeps only the most important station per area at wider views", () => {
    const station = (id: string, coordinates: [number, number], power: number, points: number) => ({
      type: "Feature" as const,
      properties: { id, power_kw: power, charging_points: points },
      geometry: { type: "Point" as const, coordinates }
    });
    const nearbySlow = station("slow", [7.01, 51.01], 22, 2);
    const nearbyFast = station("fast", [7.02, 51.02], 150, 4);
    const elsewhere = station("elsewhere", [7.6, 51.6], 50, 2);
    const stations = [nearbySlow, nearbyFast, elsewhere];

    expect(selectOfficialStationsForZoom(stations, 7)).toEqual([nearbyFast, elsewhere]);
    expect(selectOfficialStationsForZoom(stations, 10)).toEqual(stations);
  });
});
