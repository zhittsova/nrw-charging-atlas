import {
  buildDeleteAllTransaction,
  buildDeleteTransaction,
  buildInsertTransaction,
  parseTransactionResponse,
  type ProposedChargerInput,
  type WfsFeatureType
} from "./wfsTransactions";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id?: string | number;
    properties: Record<string, unknown> | null;
    geometry: unknown;
  }>;
};

export type ScenarioClientConfig = {
  wfsUrl: string;
  proposedLayer: string;
  scenarioMetricsLayer: string;
  featureType: WfsFeatureType;
};

function getFeatureUrl(wfsUrl: string, typeName: string): string {
  const query = new URLSearchParams({
    service: "WFS",
    version: "2.0.0",
    request: "GetFeature",
    typeNames: typeName,
    outputFormat: "application/json",
    srsName: "EPSG:4326"
  });
  return `${wfsUrl}?${query.toString()}`;
}

async function loadGeoJson(
  fetcher: FetchLike,
  wfsUrl: string,
  typeName: string
): Promise<GeoJsonFeatureCollection> {
  const response = await fetcher(getFeatureUrl(wfsUrl, typeName), {
    cache: "no-store",
    credentials: "same-origin"
  });
  if (!response.ok) {
    throw new Error(`GeoServer GetFeature failed (${response.status}) for ${typeName}`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("json")) {
    throw new Error(`GeoServer returned non-JSON GetFeature data for ${typeName}`);
  }
  const body = await response.json() as Partial<GeoJsonFeatureCollection>;
  if (body.type !== "FeatureCollection" || !Array.isArray(body.features)) {
    throw new Error(`GeoServer returned invalid GeoJSON for ${typeName}`);
  }
  return body as GeoJsonFeatureCollection;
}

async function transact(fetcher: FetchLike, wfsUrl: string, xml: string): Promise<string | undefined> {
  const response = await fetcher(wfsUrl, {
    method: "POST",
    headers: { "Content-Type": "text/xml; charset=UTF-8" },
    body: xml,
    credentials: "same-origin",
    cache: "no-store"
  });
  const body = await response.text();
  if (!response.ok) {
    const challenge = response.headers.get("www-authenticate");
    const result = parseTransactionResponse(body);
    const detail = result.ok
      ? "GeoServer returned a success-shaped response with a failing HTTP status"
      : result.error;
    const authentication = challenge ? `; authentication challenge: ${challenge}` : "";
    throw new Error(`GeoServer transaction failed (${response.status})${authentication}: ${detail}`);
  }
  const result = parseTransactionResponse(body);
  if (!result.ok) {
    throw new Error(`GeoServer rejected the transaction: ${result.error}`);
  }
  return result.insertedFeatureId;
}

function uuidFromFeatureId(featureId: string | undefined): string | undefined {
  const match = featureId?.match(/(?:^|\.)([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i);
  return match?.[1];
}

export function createScenarioClient(
  config: ScenarioClientConfig,
  fetcher: FetchLike = fetch
) {
  return {
    loadProposedChargers: () => loadGeoJson(fetcher, config.wfsUrl, config.proposedLayer),
    loadScenarioMetrics: () => loadGeoJson(fetcher, config.wfsUrl, config.scenarioMetricsLayer),
    async create(input: ProposedChargerInput): Promise<string> {
      const featureId = await transact(
        fetcher,
        config.wfsUrl,
        buildInsertTransaction(input, config.featureType)
      );
      const id = uuidFromFeatureId(featureId);
      if (!id) throw new Error("GeoServer inserted the station but did not return its UUID");
      return id;
    },
    async remove(id: string): Promise<void> {
      await transact(fetcher, config.wfsUrl, buildDeleteTransaction(id, config.featureType));
    },
    async reset(): Promise<void> {
      await transact(fetcher, config.wfsUrl, buildDeleteAllTransaction(config.featureType));
    }
  };
}
