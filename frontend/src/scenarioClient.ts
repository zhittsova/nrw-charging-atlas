import {
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

export type ScenarioCreateResult = {
  id: string;
  reconciled: boolean;
};

export class UncertainScenarioInsertError extends Error {
  readonly requestId: string;

  constructor(requestId: string, cause: string) {
    super(`We could not confirm whether the station was saved. Restore the service, then retry; this form will use the same safe request identifier. ${cause}`);
    this.name = "UncertainScenarioInsertError";
    this.requestId = requestId;
  }
}

class WfsTransactionError extends Error {
  readonly definiteNonCommit: boolean;

  constructor(message: string, definiteNonCommit: boolean) {
    super(message);
    this.name = "WfsTransactionError";
    this.definiteNonCommit = definiteNonCommit;
  }
}

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

function getFilteredFeatureUrl(wfsUrl: string, typeName: string, property: string, value: string): string {
  const query = new URLSearchParams({
    service: "WFS",
    version: "2.0.0",
    request: "GetFeature",
    typeNames: typeName,
    outputFormat: "application/json",
    srsName: "EPSG:4326",
    CQL_FILTER: `${property}='${value}'`
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

async function transact(fetcher: FetchLike, wfsUrl: string, xml: string, timeoutMs: number) {
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
      // A 4xx response means the request was rejected before a write. A 5xx
      // can occur after GeoServer has applied it, so callers must reconcile.
      throw new WfsTransactionError(
        `GeoServer transaction failed (${response.status})${authentication}: ${detail}`,
        response.status >= 400 && response.status < 500 || result.errorKind === "exception"
      );
    }
    const result = parseTransactionResponse(body);
    if (!result.ok) {
      throw new WfsTransactionError(
        `GeoServer rejected the transaction: ${result.error}`,
        result.errorKind === "exception"
      );
    }
    return result;
  });
}

function uuidFromFeatureId(featureId: string | undefined): string | undefined {
  const match = featureId?.match(/(?:^|\.)([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i);
  return match?.[1];
}

function mutationCountError(action: "insert" | "delete", count: number | undefined, expected: number): Error | null {
  if (count !== undefined && count !== expected) {
    return new Error(`GeoServer reported ${count} ${action === "insert" ? "inserted" : "deleted"} stations; expected exactly ${expected}`);
  }
  return null;
}

function isUncertainInsertFailure(error: unknown): boolean {
  if (error instanceof WfsTransactionError) return !error.definiteNonCommit;
  if (!(error instanceof Error)) return true;
  // An explicit 4xx denial or validation error is a definite non-commit. A
  // timeout, network failure, malformed success response, or 5xx can occur
  // after the server committed the row and must be reconciled by request_id.
  return !/GeoServer transaction failed \(4(?:00|01|03|04|09|22)\)/.test(error.message)
    && !/GeoServer rejected the transaction/.test(error.message);
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

export function createScenarioClient(
  config: ScenarioClientConfig,
  fetcher: FetchLike = fetch
) {
  const timeoutMs = config.timeoutMs ?? REQUEST_TIMEOUT_MS;
  const findById = async (id: string): Promise<GeoJsonFeatureCollection> => request(fetcher, getFilteredFeatureUrl(
    config.wfsUrl, config.proposedLayer, "id", id
  ), { cache: "no-store", credentials: "same-origin" }, timeoutMs, async (response) => {
    if (!response.ok) throw new Error(`GeoServer delete confirmation failed (${response.status})`);
    const body = await response.json() as GeoJsonFeatureCollection;
    if (body.type !== "FeatureCollection" || !Array.isArray(body.features)) {
      throw new Error("GeoServer returned invalid delete confirmation data");
    }
    return body;
  });
  return {
    // An empty proposal collection is a legitimate no-proposal state. Metrics are not.
    loadProposedChargers: () => loadGeoJson(fetcher, config.wfsUrl, config.proposedLayer, { timeoutMs }),
    loadScenarioMetrics: () => loadGeoJson(fetcher, config.wfsUrl, config.scenarioMetricsLayer, {
      requiredFields: SCENARIO_METRIC_FIELDS,
      requireFeatures: true,
      timeoutMs
    }),
    async findByRequestId(requestId: string): Promise<GeoJsonFeatureCollection> {
      return request(fetcher, getFilteredFeatureUrl(
        config.wfsUrl, config.proposedLayer, "request_id", requestId
      ), { cache: "no-store", credentials: "same-origin" }, timeoutMs, async (response) => {
        if (!response.ok) throw new Error(`GeoServer request-id lookup failed (${response.status})`);
        const body = await response.json() as GeoJsonFeatureCollection;
        if (body.type !== "FeatureCollection" || !Array.isArray(body.features)) {
          throw new Error("GeoServer returned invalid request-id lookup data");
        }
        return body;
      });
    },
    async create(input: ProposedChargerInput, options: { reconcile?: boolean } = {}): Promise<ScenarioCreateResult> {
      const xml = buildInsertTransaction(input, config.featureType);
      const findExisting = async (): Promise<ScenarioCreateResult | null> => {
        const response = await this.findByRequestId(input.requestId);
        const matches = response.features ?? [];
        if (matches.length > 1) throw new Error("Multiple proposals share this request identifier; do not retry");
        if (!matches.length) return null;
        const id = uuidFromFeatureId(String(matches[0].id ?? matches[0].properties?.id ?? ""));
        if (!id) throw new Error("The reconciled proposal has no UUID");
        return { id, reconciled: true };
      };

      if (options.reconcile) {
        const prior = await findExisting();
        if (prior) return prior;
      }
      try {
        const result = await transact(
          fetcher,
          config.wfsUrl,
          xml,
          timeoutMs
        );
        const countError = mutationCountError("insert", result.totalInserted ?? result.insertedFeatureIds?.length, 1);
        if (countError) throw countError;
        const id = uuidFromFeatureId(result.insertedFeatureId);
        if (id) return { id, reconciled: false };
        const reconciled = await findExisting();
        if (reconciled) return reconciled;
        throw new UncertainScenarioInsertError(input.requestId, "GeoServer did not return an inserted UUID.");
      } catch (error) {
        if (!isUncertainInsertFailure(error)) throw error;
        try {
          const reconciled = await findExisting();
          if (reconciled) return reconciled;
        } catch (lookupError) {
          const detail = lookupError instanceof Error ? ` Reconciliation lookup failed: ${lookupError.message}` : "";
          throw new UncertainScenarioInsertError(input.requestId, detail);
        }
        throw new UncertainScenarioInsertError(input.requestId, error instanceof Error ? error.message : "The write response was unavailable.");
      }
    },
    async remove(id: string): Promise<void> {
      const confirmAbsent = async (): Promise<void> => {
        const remaining = await findById(id);
        if (remaining.features.length) throw new Error("GeoServer did not confirm deletion of the proposed station");
      };
      let result;
      try {
        result = await transact(fetcher, config.wfsUrl, buildDeleteTransaction(id, config.featureType), timeoutMs);
      } catch (error) {
        // A lost response can follow a committed delete. Reconcile absence
        // before surfacing the original failure, without broadening the UUID
        // filter used by the delete transaction.
        try {
          await confirmAbsent();
          return;
        } catch {
          throw error;
        }
      }
      const countError = mutationCountError("delete", result.totalDeleted, 1);
      if (countError) {
        // A zero count is expected when a previous delete committed but its
        // response was lost. Only accept it after a UUID-scoped read proves
        // the station is gone; counts greater than one always remain a hard
        // safety failure.
        if (result.totalDeleted === 0) {
          try {
            await confirmAbsent();
            return;
          } catch {
            throw countError;
          }
        }
        throw countError;
      }
      await confirmAbsent();
    },
    async reset(ids: readonly string[], onRemoved?: (id: string) => void): Promise<void> {
      // Reset is intentionally a sequence of UUID-filtered deletes.  A shared
      // local scenario can contain proposals from other browser sessions, and
      // those must not be removed by this browser's cleanup action.
      for (const id of ids) {
        await this.remove(id);
        onRemoved?.(id);
      }
    }
  };
}
