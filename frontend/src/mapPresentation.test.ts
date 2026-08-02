import { describe, expect, it } from "vitest";
import {
  AUTOBAHN_STYLE,
  OVERLAY_ORDER,
  REGIONAL_ROAD_STYLE,
  officialStationStyle,
  operatorTooltip,
  scoreColor
} from "./mapPresentation";

describe("map presentation", () => {
  it("uses a non-green purple-to-coral score palette", () => {
    expect([10, 40, 55, 70, 90].map((score) => scoreColor(score, "baseline", "evReadinessScore")))
      .toEqual(["#6d28d9", "#3b82f6", "#06b6d4", "#eab308", "#f97316"]);
  });

  it("uses blue-neutral-magenta semantics for scenario change", () => {
    expect(scoreColor(12, "change", "evReadinessScore")).toBe("#2563eb");
    expect(scoreColor(0, "change", "evReadinessScore")).toBe("#64748b");
    expect(scoreColor(-12, "change", "evReadinessScore")).toBe("#be123c");
    expect(scoreColor(-12, "change", "chargerDeficitScore")).toBe("#2563eb");
  });

  it("keeps dense official stations bright without obscuring the district map", () => {
    const normal = officialStationStyle(22);
    const highPower = officialStationStyle(150);
    const statewideNormal = officialStationStyle(22, 7);
    const statewideHighPower = officialStationStyle(150, 7);

    expect(normal.pane).toBe("stations");
    expect(normal.radius).toBeGreaterThanOrEqual(2.7);
    expect(normal.radius).toBeLessThanOrEqual(3.2);
    expect(normal.fillOpacity).toBeGreaterThanOrEqual(0.9);
    expect(normal.fillColor).toBe("#22d3ee");
    expect(highPower.radius).toBeGreaterThan(normal.radius);
    expect(highPower.fillColor).toBe("#facc15");
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
      "officialStations",
      "proposedStations"
    ]);
    expect(AUTOBAHN_STYLE.weight).toBeGreaterThan(REGIONAL_ROAD_STYLE.weight);
  });
});
