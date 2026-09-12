import { describe, expect, it } from "vitest";
import { transformSync } from "esbuild";
import analyticsSource from "./analyticsPresentation.ts?raw";
import mainSource from "./main.ts?raw";
import scenarioSource from "./scenarioState.ts?raw";

function sourceFunction(source: string, name: string): string {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf("\nfunction ", start + 1);
  return source.slice(start, end === -1 ? undefined : end);
}

function createDetailRenderer() {
  const helpers = analyticsSource.replace(/export /g, "");
  const projection = scenarioSource.replace(/export /g, "");
  const detail = sourceFunction(mainSource, "renderRegionDetail");
  const dependencies = [
    "firstValue", "escapeHtml", "textValue", "numericValue", "regionName", "regionId",
    "scoreValue", "formatNumber", "formatScore", "formatMeasurement", "detailMetric",
    "unavailableReason", "scoreModelBounds"
  ].map((name) => sourceFunction(mainSource, name)).join("\n");
  const program = `${helpers}\n${projection}\n${dependencies}\n${detail}\n
    let scenarioViewMode = 'baseline';
    let scoreModel = [];
    const scoreKeys = {
      investmentPriorityScore: ['investment_priority_score'],
      evReadinessScore: ['ev_readiness_score'],
      chargerDeficitScore: ['charger_deficit_score'],
      infrastructureOpportunityScore: ['infrastructure_opportunity_score']
    };
    const scoreColor = () => '#000';
    const panel = { innerHTML: '' };
    const document = { getElementById: () => panel };
    let baselineRegionFeatures = [];
    return {
      render(properties, baseline, mode) {
        scenarioViewMode = mode;
        baselineRegionFeatures = [{ properties: baseline }];
        renderRegionDetail({ properties });
        return panel.innerHTML;
      },
      projectScenarioProperties
    };
  `;
  return new Function("Intl", transformSync(program, { loader: "ts", format: "cjs" }).code)(Intl) as {
    render: (properties: Record<string, unknown>, baseline: Record<string, unknown>, mode: "baseline" | "scenario") => string;
    projectScenarioProperties: (properties: Record<string, unknown>, mode: "scenario") => Record<string, unknown>;
  };
}

describe("district detail renderer", () => {
  it("uses resolved baseline renewable capacity in both summaries for baseline and scenario known, zero and null values", () => {
    const renderer = createDetailRenderer();

    for (const capacity of [511.3, 0, null]) {
      const baseline = {
        nuts_code: "DEA05",
        district_name: "Fixture",
        investment_priority_score: 42,
        priority_rank: 1,
        operating_asset_renewable_capacity_mw: capacity
      };
      const scenario = renderer.projectScenarioProperties({
        nuts_code: "DEA05",
        district_name: "Fixture",
        scenario_investment_priority_score: 42,
        scenario_priority_rank: 1,
        infrastructure_opportunity_score: 64.4
      }, "scenario");
      const summaryValue = capacity === null ? "— MW" : `${capacity} MW`;
      const evidenceValue = capacity === null ? "—" : `${capacity} MW`;

      for (const html of [renderer.render(baseline, baseline, "baseline"), renderer.render(scenario, baseline, "scenario")]) {
        expect(html).toContain(`<strong>${summaryValue}</strong> of existing renewable capacity`);
        expect(html).toContain(`Operating-asset renewable capacity</span><strong>${evidenceValue}</strong>`);
      }
    }
  });
});
