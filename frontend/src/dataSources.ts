export type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id?: string | number;
    properties: Record<string, unknown> | null;
    geometry: GeoJSON.Geometry | null;
  }>;
};

export type LayerState = "live" | "snapshot" | "stale" | "unavailable";

export type LayerRead<T> = {
  state: LayerState;
  data?: T;
  detail: string;
};

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

// The full official station layer contains more than 21,000 features. Keep a
// finite request/body deadline, while allowing a healthy local WFS to deliver
// the complete canonical response instead of unnecessarily falling back.
export const DEFAULT_LAYER_READ_TIMEOUT_MS = 30_000;

export const CANONICAL_DISTRICT_FIELDS = [
  "nuts_code", "district_name", "chargers_total", "charging_points_total",
  "fast_chargers_total", "normal_chargers_total", "unknown_power_chargers_total", "priority_rank",
  "ev_readiness_score", "charger_deficit_score", "infrastructure_opportunity_score",
  "investment_priority_score", "data_quality_flag", "formula_version",
  "population_source_year", "transport_data_quality_flag", "grid_data_quality_flag",
  "energy_data_quality_flag", "operating_asset_renewable_capacity_mw",
  "municipal_workbook_renewable_capacity_mw", "transport_load_score",
  "traffic_intensity_score", "traffic_road_density_score", "road_proximity_score",
  "grid_readiness_proxy_score", "substation_proximity_score", "voltage_line_density_score",
  "substation_density_score", "renewable_context_score", "renewable_capacity_density_score",
  "renewable_technology_diversity_score", "local_energy_balance_score",
  "renewable_growth_score", "grid_absorption_risk_proxy_score", "energy_reporting_year",
  "charger_snapshot_date", "charger_snapshot_unavailable_reason",
  "consumption_municipal_coverage", "renewable_municipal_coverage",
  "municipal_workbook_renewable_capacity_coverage", "energy_unavailable_reason",
  "traffic_intensity_dtv", "traffic_weighted_road_density", "traffic_road_length_km",
  "traffic_measured_length_km", "traffic_length_coverage", "distance_to_nearest_road_m",
  "voltage_weighted_line_density", "substation_density", "distance_to_nearest_substation_m",
  "grid_line_length_km", "substation_count", "maximum_mapped_voltage_kv",
  "line_voltage_coverage", "substation_voltage_coverage",
  "operating_asset_renewable_capacity_mw_per_km2", "renewable_installation_count",
  "renewable_technology_count", "consumption_mwh", "published_generation_mwh",
  "estimated_wind_generation_mwh", "total_renewable_generation_mwh", "renewable_balance_ratio",
  "renewable_coverage_pct", "renewable_net_addition_3y_mw",
  "renewable_growth_density_mw_per_km2", "expected_municipalities", "growth_years_required",
  "growth_years_reported", "wind_full_load_hours", "renewable_growth_window_years",
  "wind_estimate_note", "municipal_workbook_capacity_reporting_year",
  "municipal_workbook_renewable_capacity_unavailable_reason", "charger_snapshot_source",
  "formula_version_date", "energy_source"
] as const;

export const CANONICAL_STATION_FIELDS = [
  "source_id", "operator", "power_kw", "max_point_power_kw", "charging_points", "charger_type", "status"
] as const;

export class LayerReadError extends Error {
  constructor(
    readonly kind: "missing-publication" | "transport" | "schema",
    message: string
  ) {
    super(message);
  }
}

function isFeatureCollection(value: unknown): value is GeoJsonFeatureCollection {
  return Boolean(value)
    && typeof value === "object"
    && (value as { type?: unknown }).type === "FeatureCollection"
    && Array.isArray((value as { features?: unknown }).features);
}

function isPosition(value: unknown): value is GeoJSON.Position {
  return Array.isArray(value)
    && value.length >= 2
    && value.every((coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate));
}

function isLineString(value: unknown): value is GeoJSON.Position[] {
  return Array.isArray(value) && value.length >= 2 && value.every(isPosition);
}

function isLinearRing(value: unknown): value is GeoJSON.Position[] {
  if (!Array.isArray(value) || value.length < 4 || !value.every(isPosition)) return false;
  const first = value[0];
  const last = value[value.length - 1];
  return first.length === last.length && first.every((coordinate, index) => coordinate === last[index]);
}

function isGeometry(value: unknown): value is GeoJSON.Geometry {
  if (!value || typeof value !== "object") return false;
  const geometry = value as { type?: unknown; coordinates?: unknown; geometries?: unknown };
  switch (geometry.type) {
    case "Point":
      return isPosition(geometry.coordinates);
    case "MultiPoint":
      return Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0
        && geometry.coordinates.every(isPosition);
    case "LineString":
      return isLineString(geometry.coordinates);
    case "MultiLineString":
      return Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0
        && geometry.coordinates.every(isLineString);
    case "Polygon":
      return Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0
        && geometry.coordinates.every(isLinearRing);
    case "MultiPolygon":
      return Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0
        && geometry.coordinates.every((polygon) => Array.isArray(polygon) && polygon.length > 0
          && polygon.every(isLinearRing));
    case "GeometryCollection":
      return Array.isArray(geometry.geometries) && geometry.geometries.length > 0 && geometry.geometries.every(isGeometry);
    default:
      return false;
  }
}

export function validateGeoJson(
  value: unknown,
  requiredFields: readonly string[],
  label: string
): GeoJsonFeatureCollection {
  if (!isFeatureCollection(value)) {
    throw new LayerReadError("schema", `${label} did not return a GeoJSON FeatureCollection`);
  }
  const hasInvalidFeature = value.features.some((feature) => {
    if (!feature || feature.type !== "Feature" || !feature.properties || typeof feature.properties !== "object" || !isGeometry(feature.geometry)) return true;
    return requiredFields.some((field) => !(field in feature.properties));
  });
  if (hasInvalidFeature) {
    throw new LayerReadError("schema", `${label} has an invalid feature or is missing required canonical properties`);
  }
  return value;
}

async function fetchJson(
  path: string,
  fetcher: FetchLike,
  timeoutMs: number
): Promise<unknown> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(path, {
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal
    });
    if (!response.ok) {
      const kind = response.status === 400 || response.status === 404
        ? "missing-publication"
        : "transport";
      throw new LayerReadError(kind, `${path} returned HTTP ${response.status}`);
    }
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType && !contentType.toLowerCase().includes("json")) {
      throw new LayerReadError("schema", `${path} returned non-JSON data`);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof LayerReadError) throw error;
    const message = error instanceof Error && error.name === "AbortError"
      ? `${path} timed out after ${timeoutMs}ms`
      : `${path} could not be reached`;
    throw new LayerReadError("transport", message);
  } finally {
    clearTimeout(timeout);
  }
}

function snapshotState(manifest: unknown): "snapshot" | "stale" {
  if (!manifest || typeof manifest !== "object") return "snapshot";
  const generatedAt = (manifest as { generated_at?: unknown }).generated_at;
  const generated = typeof generatedAt === "string" ? Date.parse(generatedAt) : Number.NaN;
  return Number.isFinite(generated) && Date.now() - generated > 1000 * 60 * 60 * 24 * 30
    ? "stale"
    : "snapshot";
}

export async function readLayer(
  primaryPath: string,
  snapshotPath: string,
  requiredFields: readonly string[],
  options: { fetcher?: FetchLike; timeoutMs?: number; manifestPath?: string; preferSnapshot?: boolean } = {}
): Promise<LayerRead<GeoJsonFeatureCollection>> {
  const fetcher = options.fetcher ?? fetch;
  const timeoutMs = options.timeoutMs ?? DEFAULT_LAYER_READ_TIMEOUT_MS;
  const readSnapshot = async (liveReason?: LayerReadError): Promise<LayerRead<GeoJsonFeatureCollection>> => {
    try {
      const [snapshot, manifest] = await Promise.all([
        fetchJson(snapshotPath, fetcher, timeoutMs),
        options.manifestPath ? fetchJson(options.manifestPath, fetcher, timeoutMs).catch(() => undefined) : Promise.resolve(undefined)
      ]);
      const state = snapshotState(manifest);
      return {
        state,
        data: validateGeoJson(snapshot, requiredFields, snapshotPath),
        detail: liveReason
          ? `${state === "stale" ? "Stale" : "Canonical"} snapshot after ${liveReason.kind}: ${liveReason.message}`
          : `${state === "stale" ? "Stale" : "Canonical"} snapshot selected for optional layer`
      };
    } catch (snapshotError) {
      const snapshotReason = snapshotError instanceof LayerReadError
        ? snapshotError.message
        : "snapshot could not be read";
      return {
        state: "unavailable",
        detail: liveReason
          ? `Unavailable: ${liveReason.kind} WFS failure; ${snapshotReason}`
          : `Unavailable: canonical snapshot failure; ${snapshotReason}`
      };
    }
  };
  if (options.preferSnapshot) return readSnapshot();
  try {
    return {
      state: "live",
      data: validateGeoJson(await fetchJson(primaryPath, fetcher, timeoutMs), requiredFields, primaryPath),
      detail: "Live WFS"
    };
  } catch (liveError) {
    const reason = liveError instanceof LayerReadError ? liveError : new LayerReadError("transport", "WFS unavailable");
    return readSnapshot(reason);
  }
}
