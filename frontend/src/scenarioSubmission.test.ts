import { describe, expect, it, vi } from "vitest";

import { createScenarioClient, UncertainScenarioInsertError } from "./scenarioClient";
import { ScenarioDialogState } from "./scenarioDialogState";
import { submitScenarioProposal } from "./scenarioSubmission";

const config = {
  wfsUrl: "http://localhost:8080/geoserver/ows",
  proposedLayer: "nrw:proposed_chargers",
  scenarioMetricsLayer: "nrw:nrw_ev_scenario_metrics",
  featureType: {
    prefix: "nrw",
    namespaceUri: "https://nrw.local/scenario",
    typeName: "proposed_chargers"
  }
};

const requestId = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";
const savedId = "123e4567-e89b-12d3-a456-426614174000";
const input = {
  name: "Recovery", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
  longitude: 7.2, latitude: 51.2
};

function recoveredFeature() {
  return new Response(JSON.stringify({
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      id: `proposed_chargers.${savedId}`,
      properties: { id: savedId, request_id: requestId },
      geometry: { type: "Point", coordinates: [7.2, 51.2] }
    }]
  }), { headers: { "Content-Type": "application/json" } });
}

describe("scenario submit recovery", () => {
  it("keeps lookup-first retries after an uncertain save and a failed reconciliation lookup", async () => {
    let reads = 0;
    const fetcher = vi.fn().mockImplementation(async (_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return new Response("<html>gateway response</html>", { status: 200 });
      reads += 1;
      if (reads < 3) throw new TypeError("service unavailable");
      return recoveredFeature();
    });
    const client = createScenarioClient(config, fetcher);
    const state = new ScenarioDialogState();
    const submit = () => submitScenarioProposal({ client, dialogState: state, createRequestId: () => requestId, input });

    await expect(submit()).rejects.toBeInstanceOf(UncertainScenarioInsertError);
    await expect(submit()).rejects.toThrow("service unavailable");
    await expect(submit()).resolves.toMatchObject({
      operation: { requestId, reconcile: true },
      saved: { id: savedId, reconciled: true }
    });

    expect(fetcher.mock.calls.map(([, init]) => init?.method === "POST" ? "POST" : "GET"))
      .toEqual(["POST", "GET", "GET", "GET"]);
  });

  it("keeps lookup-first recovery after a saved write has a refresh-pending outcome", async () => {
    let reads = 0;
    const fetcher = vi.fn().mockImplementation(async (_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response(`<wfs:WFS_TransactionResponse xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc">
          <wfs:InsertResult><ogc:FeatureId fid="proposed_chargers.${savedId}"/></wfs:InsertResult>
          <wfs:TransactionResult><wfs:Status><wfs:SUCCESS/></wfs:Status></wfs:TransactionResult>
        </wfs:WFS_TransactionResponse>`, { status: 200 });
      }
      reads += 1;
      if (reads === 1) throw new TypeError("service unavailable");
      return recoveredFeature();
    });
    const client = createScenarioClient(config, fetcher);
    const state = new ScenarioDialogState();
    const submit = () => submitScenarioProposal({ client, dialogState: state, createRequestId: () => requestId, input });

    await expect(submit()).resolves.toMatchObject({ saved: { reconciled: false } });
    // This mirrors main.ts after a saved write whose indicator refresh fails.
    state.finishSubmission({ needsReconciliation: true });
    await expect(submit()).rejects.toThrow("service unavailable");
    await expect(submit()).resolves.toMatchObject({
      operation: { requestId, reconcile: true },
      saved: { id: savedId, reconciled: true }
    });

    expect(fetcher.mock.calls.map(([, init]) => init?.method === "POST" ? "POST" : "GET"))
      .toEqual(["POST", "GET", "GET"]);
  });
});
