import { describe, expect, it, vi } from "vitest";

import { createScenarioClient } from "./scenarioClient";


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

describe("scenario WFS client", () => {
  it("loads proposed chargers as GeoJSON without browser caching", async () => {
    const responseBody = { type: "FeatureCollection", features: [] };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.loadProposedChargers()).resolves.toEqual(responseBody);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toContain("request=GetFeature");
    expect(url).toContain("typeNames=nrw%3Aproposed_chargers");
    expect(options).toMatchObject({ cache: "no-store", credentials: "same-origin" });
  });

  it("loads the scenario district metrics layer", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { nuts_code: "DEA01" }, geometry: null }]
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = createScenarioClient(config, fetcher);

    const result = await client.loadScenarioMetrics();

    expect(result.features).toHaveLength(1);
    expect(fetcher.mock.calls[0][0]).toContain("typeNames=nrw%3Anrw_ev_scenario_metrics");
  });

  it("creates a proposed charger and returns its database UUID", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(`
      <wfs:WFS_TransactionResponse xmlns:wfs="http://www.opengis.net/wfs"
        xmlns:ogc="http://www.opengis.net/ogc">
        <wfs:InsertResult><ogc:FeatureId fid="proposed_chargers.f6e942dc-faf7-4db6-b332-928ad2cb86e3"/></wfs:InsertResult>
        <wfs:TransactionResult><wfs:Status><wfs:SUCCESS/></wfs:Status></wfs:TransactionResult>
      </wfs:WFS_TransactionResponse>
    `, { status: 200 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Demo", chargingPoints: 4, powerKw: 150, longitude: 7.2, latitude: 51.2
    })).resolves.toBe("f6e942dc-faf7-4db6-b332-928ad2cb86e3");
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe(config.wfsUrl);
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({ "Content-Type": "text/xml; charset=UTF-8" });
    expect(options.body).toContain("<wfs:Insert>");
  });

  it("deletes one station and resets all proposed stations", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(`
      <wfs:WFS_TransactionResponse xmlns:wfs="http://www.opengis.net/wfs">
        <wfs:TransactionResult><wfs:Status><wfs:SUCCESS/></wfs:Status></wfs:TransactionResult>
      </wfs:WFS_TransactionResponse>
    `, { status: 200 }));
    const client = createScenarioClient(config, fetcher);
    const id = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";

    await client.remove(id);
    await client.reset();

    expect(fetcher.mock.calls[0][1].body).toContain(`<ogc:Literal>${id}</ogc:Literal>`);
    expect(fetcher.mock.calls[1][1].body).toContain("<ogc:Literal>proposed</ogc:Literal>");
  });

  it("surfaces a GeoServer exception even when the HTTP status is 200", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(`
      <ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows">
        <ows:Exception><ows:ExceptionText>Point is outside NRW</ows:ExceptionText></ows:Exception>
      </ows:ExceptionReport>
    `, { status: 200 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Outside", chargingPoints: 2, powerKw: 22, longitude: 10, latitude: 54
    })).rejects.toThrow("Point is outside NRW");
  });

  it("rejects non-JSON GetFeature responses with an actionable error", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("proxy error", { status: 502 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.loadProposedChargers()).rejects.toThrow("GeoServer GetFeature failed (502)");
  });
});
