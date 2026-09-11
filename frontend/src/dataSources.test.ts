import { describe, expect, it, vi } from "vitest";
import {
  CANONICAL_DISTRICT_FIELDS,
  CANONICAL_STATION_FIELDS,
  readLayer
} from "./dataSources";

const district = {
  type: "Feature",
  properties: Object.fromEntries(CANONICAL_DISTRICT_FIELDS.map((field) => [field, field === "ev_readiness_score" ? null : "value"])),
  geometry: { type: "Point", coordinates: [7, 51] }
};
const station = {
  type: "Feature",
  properties: Object.fromEntries(CANONICAL_STATION_FIELDS.map((field) => [field, field === "max_point_power_kw" ? null : "value"])),
  geometry: { type: "Point", coordinates: [7, 51] }
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" }
});

describe("canonical layer reads", () => {
  it("uses a bounded canonical WFS response when its schema is present", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ type: "FeatureCollection", features: [district] }));
    const result = await readLayer("/geoserver/ows?typeNames=nrw:nrw_ev_baseline_metrics", "/data/nrw_regions_sample.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher });
    expect(result.state).toBe("live");
    expect(result.data?.features[0].properties?.ev_readiness_score).toBeNull();
    expect(fetcher.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  });

  it("preserves every canonical district including null values without manufacturing zeroes", async () => {
    const features = Array.from({ length: 53 }, (_, index) => ({
      ...district,
      properties: { ...district.properties, nuts_code: `DEA${String(index).padStart(2, "0")}` }
    }));
    const fetcher = vi.fn().mockResolvedValue(response({ type: "FeatureCollection", features }));
    const result = await readLayer("/districts", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher });
    expect(result.data?.features).toHaveLength(53);
    expect(result.data?.features[0].properties?.ev_readiness_score).toBeNull();
  });

  it("labels a valid canonical snapshot and distinguishes a stale manifest", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({}, 404))
      .mockResolvedValueOnce(response({ type: "FeatureCollection", features: [station] }))
      .mockResolvedValueOnce(response({ generated_at: "2000-01-01T00:00:00Z" }));
    const result = await readLayer("/wfs", "/data/nrw_charging_stations_sample.geojson", CANONICAL_STATION_FIELDS, { fetcher, manifestPath: "/data/manifest.json" });
    expect(result.state).toBe("stale");
    expect(result.detail).toContain("missing-publication");
  });

  it("uses the validated snapshot without starting an optional live WFS read", async () => {
    const fetcher = vi.fn(async (path: RequestInfo | URL) => {
      if (String(path) === "/data/districts.geojson") return response({ type: "FeatureCollection", features: [district] });
      if (String(path) === "/data/manifest.json") return response({ generated_at: new Date().toISOString() });
      throw new Error(`unexpected request: ${String(path)}`);
    });

    const result = await readLayer("/expensive-optional-wfs", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, {
      fetcher,
      manifestPath: "/data/manifest.json",
      preferSnapshot: true
    });

    expect(result.state).toBe("snapshot");
    expect(result.detail).toContain("snapshot selected for optional layer");
    expect(fetcher).not.toHaveBeenCalledWith("/expensive-optional-wfs", expect.anything());
  });

  it("does not invent a substitute when both WFS and snapshot are absent", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({}, 502))
      .mockResolvedValueOnce(response({}, 404));
    const result = await readLayer("/wfs", "/data/missing.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher });
    expect(result).toEqual(expect.objectContaining({ state: "unavailable" }));
    expect(result.data).toBeUndefined();
  });

  it("rejects null features and falls back instead of passing them to the map", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({ type: "FeatureCollection", features: [null] }))
      .mockResolvedValueOnce(response({ type: "FeatureCollection", features: [district] }));
    const result = await readLayer("/wfs", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher });
    expect(result.state).toBe("snapshot");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("rejects a geometrically invalid feature before it reaches Leaflet", async () => {
    const malformedDistrict = { ...district, geometry: { type: "Polygon", coordinates: [[[7, 51]]] } };
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({ type: "FeatureCollection", features: [malformedDistrict] }))
      .mockResolvedValueOnce(response({ type: "FeatureCollection", features: [district] }));
    await expect(readLayer("/wfs", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher }))
      .resolves.toMatchObject({ state: "snapshot" });
  });

  it("keeps the timeout active while a response body is still streaming", async () => {
    const fetcher = vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal!;
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          signal.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
        }
      }), { headers: { "content-type": "application/json" } }));
    });
    const result = await readLayer("/wfs", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, {
      fetcher,
      timeoutMs: 20
    });
    expect(result.state).toBe("unavailable");
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it("keeps an optional layer failure independent from district and station reads", async () => {
    const fetcher = vi.fn((path: string) => {
      if (path.includes("renewables")) return Promise.resolve(response({}, 502));
      if (path.includes("missing")) return Promise.resolve(response({}, 404));
      return Promise.resolve(response({ type: "FeatureCollection", features: [district] }));
    });
    const [districtResult, optionalResult] = await Promise.all([
      readLayer("/districts", "/data/districts.geojson", CANONICAL_DISTRICT_FIELDS, { fetcher }),
      readLayer("/renewables", "/data/missing-renewables.geojson", ["source_id"], { fetcher })
    ]);
    expect(districtResult.state).toBe("live");
    expect(optionalResult.state).toBe("unavailable");
  });
});
