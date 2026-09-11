import { describe, expect, it, vi } from "vitest";

import { createScenarioClient, SCENARIO_METRIC_FIELDS } from "./scenarioClient";


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
  const scenarioMetric = {
    type: "Feature",
    properties: Object.fromEntries(SCENARIO_METRIC_FIELDS.map((field) => [field, field === "nuts_code" ? "DEA01" : null])),
    geometry: { type: "Point", coordinates: [7, 51] }
  };

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
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("loads the scenario district metrics layer", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      type: "FeatureCollection",
      features: [scenarioMetric]
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = createScenarioClient(config, fetcher);

    const result = await client.loadScenarioMetrics();

    expect(result.features).toHaveLength(1);
    expect(fetcher.mock.calls[0][0]).toContain("typeNames=nrw%3Anrw_ev_scenario_metrics");
  });

  it("rejects empty scenario metrics while allowing empty proposals", async () => {
    const emptyMetrics = vi.fn().mockResolvedValue(new Response(JSON.stringify({ type: "FeatureCollection", features: [] }), {
      status: 200, headers: { "Content-Type": "application/json" }
    }));
    await expect(createScenarioClient(config, emptyMetrics).loadScenarioMetrics()).rejects.toThrow("incomplete GeoJSON");

    const emptyProposals = vi.fn().mockResolvedValue(new Response(JSON.stringify({ type: "FeatureCollection", features: [] }), {
      status: 200, headers: { "Content-Type": "application/json" }
    }));
    await expect(createScenarioClient(config, emptyProposals).loadProposedChargers()).resolves.toMatchObject({ features: [] });
  });

  it("keeps the scenario request timeout active until a streaming body completes", async () => {
    const fetcher = vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal!;
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          signal.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
        }
      }), { headers: { "Content-Type": "application/json" } }));
    });
    const client = createScenarioClient({ ...config, timeoutMs: 20 }, fetcher);
    await expect(client.loadScenarioMetrics()).rejects.toThrow("timed out after 20ms");
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
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
      name: "Demo", chargingPoints: 4, powerKw: 150, maxPointPowerKw: 75,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7.2, latitude: 51.2
    })).resolves.toEqual({ id: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", reconciled: false });
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe(config.wfsUrl);
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({ "Content-Type": "text/xml; charset=UTF-8" });
    expect(options.body).toContain("<wfs:Insert>");
    expect(options.body).toContain("<nrw:max_point_power_kw>75</nrw:max_point_power_kw>");
    expect(options.body).toContain("<nrw:request_id>f6e942dc-faf7-4db6-b332-928ad2cb86e3</nrw:request_id>");
  });

  it("deletes one station and resets all proposed stations", async () => {
    const fetcher = vi.fn().mockImplementation(async (_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== "POST") {
        return new Response(JSON.stringify({ type: "FeatureCollection", features: [] }), {
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(`
      <wfs:WFS_TransactionResponse xmlns:wfs="http://www.opengis.net/wfs">
        <wfs:TransactionResult><wfs:Status><wfs:SUCCESS/></wfs:Status></wfs:TransactionResult>
      </wfs:WFS_TransactionResponse>
    `, { status: 200 });
    });
    const client = createScenarioClient(config, fetcher);
    const id = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";

    await client.remove(id);
    await client.reset([id]);

    expect(fetcher.mock.calls[0][1].body).toContain(`<ogc:Literal>${id}</ogc:Literal>`);
    expect(fetcher.mock.calls[2][1].body).toContain(`<ogc:Literal>${id}</ogc:Literal>`);
  });

  it("surfaces a GeoServer exception even when the HTTP status is 200", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(`
      <ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows">
        <ows:Exception><ows:ExceptionText>Point is outside NRW</ows:ExceptionText></ows:Exception>
      </ows:ExceptionReport>
    `, { status: 200 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Outside", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 10, latitude: 54
    })).rejects.toThrow("Point is outside NRW");
  });

  it("reports the actual 401 authentication challenge instead of a parser fallback", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("<html>Unauthorized</html>", {
      status: 401,
      headers: { "WWW-Authenticate": "Basic realm=GeoServer" }
    }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Denied", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7.2, latitude: 51.2
    })).rejects.toThrow("GeoServer transaction failed (401); authentication challenge: Basic realm=GeoServer");
  });

  it("reports a non-XML 403 response as an authorization failure without a retry", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("forbidden by gateway", { status: 403 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Forbidden", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7.2, latitude: 51.2
    })).rejects.toThrow("GeoServer transaction failed (403): GeoServer returned an unrecognized transaction response");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reconciles a timeout-after-commit by request identifier without a second insert", async () => {
    const requestId = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";
    const fetcher = vi.fn().mockImplementation(async (_: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") throw new TypeError("network connection closed");
      return new Response(JSON.stringify({
        type: "FeatureCollection",
        features: [{
          type: "Feature",
          id: "proposed_chargers.123e4567-e89b-12d3-a456-426614174000",
          properties: { id: "123e4567-e89b-12d3-a456-426614174000", request_id: requestId },
          geometry: { type: "Point", coordinates: [7.2, 51.2] }
        }]
      }), { headers: { "Content-Type": "application/json" } });
    });
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Interrupted", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId, longitude: 7.2, latitude: 51.2
    })).resolves.toEqual({ id: "123e4567-e89b-12d3-a456-426614174000", reconciled: true });
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(String(fetcher.mock.calls[1][0])).toContain("CQL_FILTER=request_id%3D%27f6e942dc");
  });

  it("looks up the retained request identifier before a recovery retry", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: "proposed_chargers.123e4567-e89b-12d3-a456-426614174000",
        properties: { id: "123e4567-e89b-12d3-a456-426614174000" },
        geometry: { type: "Point", coordinates: [7.2, 51.2] }
      }]
    }), { headers: { "Content-Type": "application/json" } }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.create({
      name: "Retry", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7.2, latitude: 51.2
    }, { reconcile: true })).resolves.toEqual({ id: "123e4567-e89b-12d3-a456-426614174000", reconciled: true });
    expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  });

  it("rejects non-JSON GetFeature responses with an actionable error", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("proxy error", { status: 502 }));
    const client = createScenarioClient(config, fetcher);

    await expect(client.loadProposedChargers()).rejects.toThrow("GeoServer GetFeature failed (502)");
  });
});
