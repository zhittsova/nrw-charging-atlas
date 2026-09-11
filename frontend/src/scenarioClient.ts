import {
  buildDeleteAllTransaction,
  buildDeleteTransaction,
  buildInsertTransaction,
  parseTransactionResponse,
  type ProposedChargerInput,
  type WfsFeatureType
} from "./wfsTransactions";
import { validateGeoJson } from "./dataSources";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
const REQUEST_TIMEOUT_MS = 12_000;

export const SCENARIO_METRIC_FIELDS = [
  "nuts_code",
  "baseline_chargers_total", "scenario_chargers_total", "chargers_total_delta",
  "baseline_charging_points_total", "scenario_charging_points_total", "charging_points_total_delta",
  "baseline_fast_chargers_total", "scenario_fast_chargers_total", "fast_chargers_total_delta",
  "baseline_normal_chargers_total", "scenario_normal_chargers_total", "normal_chargers_total_delta",
  "baseline_unknown_power_chargers_total", "scenario_unknown_power_chargers_total", "unknown_power_chargers_total_delta",
  "baseline_chargers_per_km2", "scenario_chargers_per_km2", "chargers_per_km2_delta",
  "baseline_charging_points_per_100k_population", "scenario_charging_points_per_100k_population", "charging_points_per_100k_population_delta",
  "baseline_distance_to_nearest_charger_m", "scenario_distance_to_nearest_charger_m", "distance_to_nearest_charger_m_delta",
  "baseline_charger_density_score", "scenario_charger_density_score", "charger_density_score_delta",
  "baseline_charger_accessibility_score", "scenario_charger_accessibility_score", "charger_accessibility_score_delta",
  "baseline_population_adjusted_coverage_score", "scenario_population_adjusted_coverage_score", "population_adjusted_coverage_score_delta",
  "baseline_ev_readiness_score", "scenario_ev_readiness_score", "ev_readiness_score_delta",
  "baseline_charger_deficit_score", "scenario_charger_deficit_score", "charger_deficit_score_delta",
  "infrastructure_opportunity_score",
  "baseline_investment_priority_score", "scenario_investment_priority_score", "investment_priority_score_delta",
  "baseline_priority_rank", "scenario_priority_rank"
] as const;

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
  timeoutMs?: number;
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
  typeName: string,
  options: { requiredFields?: readonly string[]; requireFeatures?: boolean; timeoutMs: number }
): Promise<GeoJsonFeatureCollection> {
  return request(fetcher, getFeatureUrl(wfsUrl, typeName), {
    cache: "no-store",
    credentials: "same-origin"
  }, options.timeoutMs, async (response) => {
    if (!response.ok) {
      throw new Error(`GeoServer GetFeature failed (${response.status}) for ${typeName}`);
    }
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType && !contentType.toLowerCase().includes("json")) {
      throw new Error(`GeoServer returned non-JSON GetFeature data for ${typeName}`);
    }
    const body = await response.json() as Partial<GeoJsonFeatureCollection>;
    if (body.type !== "FeatureCollection" || !Array.isArray(body.features)
      || (options.requireFeatures && body.features.length === 0)) {
      throw new Error(`GeoServer returned incomplete GeoJSON for ${typeName}`);
    }
    validateGeoJson(body, options.requiredFields ?? [], typeName);
    return body as GeoJsonFeatureCollection;
  });
}

async function transact(fetcher: FetchLike, wfsUrl: string, xml: string, timeoutMs: number): Promise<string | undefined> {
  return request(fetcher, wfsUrl, {
    method: "POST",
    headers: { "Content-Type": "text/xml; charset=UTF-8" },
    body: xml,
    credentials: "same-origin",
    cache: "no-store"
  }, timeoutMs, async (response) => {
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
  });
}

async function request<T>(
  fetcher: FetchLike,
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
  read: (response: Response) => Promise<T>
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(input, { ...init, signal: controller.signal });
    return await read(response);
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error(`GeoServer request timed out after ${timeoutMs}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function uuidFromFeatureId(featureId: string | undefined): string | undefined {
  const match = featureId?.match(/(?:^|\.)([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i);
  return match?.[1];
}

export function createScenarioClient(
  config: ScenarioClientConfig,
  fetcher: FetchLike = fetch
) {
  const timeoutMs = config.timeoutMs ?? REQUEST_TIMEOUT_MS;
  return {
    // An empty proposal collection is a legitimate no-proposal state. Metrics are not.
    loadProposedChargers: () => loadGeoJson(fetcher, config.wfsUrl, config.proposedLayer, { timeoutMs }),
    loadScenarioMetrics: () => loadGeoJson(fetcher, config.wfsUrl, config.scenarioMetricsLayer, {
      requiredFields: SCENARIO_METRIC_FIELDS,
      requireFeatures: true,
      timeoutMs
    }),
    async create(input: ProposedChargerInput): Promise<string> {
      const featureId = await transact(
        fetcher,
        config.wfsUrl,
        buildInsertTransaction(input, config.featureType),
        timeoutMs
      );
      const id = uuidFromFeatureId(featureId);
      if (!id) throw new Error("GeoServer inserted the station but did not return its UUID");
      return id;
    },
    async remove(id: string): Promise<void> {
      await transact(fetcher, config.wfsUrl, buildDeleteTransaction(id, config.featureType), timeoutMs);
    },
    async reset(): Promise<void> {
      await transact(fetcher, config.wfsUrl, buildDeleteAllTransaction(config.featureType), timeoutMs);
    }
  };
}
