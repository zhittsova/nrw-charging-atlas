export type NumericProperties = Record<string, unknown>;

export type FastShare =
  | { available: true; percent: number; fast: number; total: number }
  | { available: false; reason: "zero_total" | "incomplete_classification" | "missing_counts" };

export type FastShareChange =
  | { available: true; baseline: FastShare & { available: true }; scenario: FastShare & { available: true }; percentagePoints: number }
  | { available: false; baseline: FastShare; scenario: FastShare };

export function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Canonical coverage fields are ratios, while the UI states percentages. */
export function formatCoveragePercentage(value: unknown): string {
  const ratio = finiteNumber(value);
  return ratio === null ? "—" : `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(ratio * 100)}%`;
}

/** A rank is meaningful only when its corresponding published priority exists. */
export function publishedPriorityRank(priority: unknown, rank: unknown): number | null {
  return finiteNumber(priority) === null ? null : finiteNumber(rank);
}

/**
 * Fast is defined from maximum point power. Unknown point power means the
 * statewide share cannot be stated exactly, even when a station total exists.
 */
export function fastShareFromCounts(counts: {
  total: unknown;
  fast: unknown;
  unknownPower: unknown;
}): FastShare {
  const total = finiteNumber(counts.total);
  const fast = finiteNumber(counts.fast);
  const unknownPower = finiteNumber(counts.unknownPower);
  if (total === null || fast === null || unknownPower === null || total < 0 || fast < 0 || unknownPower < 0) {
    return { available: false, reason: "missing_counts" };
  }
  if (total === 0) return { available: false, reason: "zero_total" };
  if (fast + unknownPower > total || unknownPower > 0) {
    return { available: false, reason: "incomplete_classification" };
  }
  return { available: true, percent: 100 * fast / total, fast, total };
}

export function statewideFastShare(rows: NumericProperties[], prefix = ""): FastShare {
  let total = 0;
  let fast = 0;
  let unknownPower = 0;
  for (const row of rows) {
    const rowTotal = finiteNumber(row[`${prefix}chargers_total`]);
    const rowFast = finiteNumber(row[`${prefix}fast_chargers_total`]);
    const rowUnknown = finiteNumber(row[`${prefix}unknown_power_chargers_total`]);
    if (rowTotal === null || rowFast === null || rowUnknown === null) {
      return { available: false, reason: "missing_counts" };
    }
    total += rowTotal;
    fast += rowFast;
    unknownPower += rowUnknown;
  }
  return fastShareFromCounts({ total, fast, unknownPower });
}

export function fastShareChange(rows: NumericProperties[]): FastShareChange {
  const baseline = statewideFastShare(rows, "baseline_");
  const scenario = statewideFastShare(rows, "scenario_");
  if (!baseline.available || !scenario.available) return { available: false, baseline, scenario };
  return { available: true, baseline, scenario, percentagePoints: scenario.percent - baseline.percent };
}

export function fastShareReason(share: FastShare): string {
  if (share.available === true) return "";
  const { reason } = share;
  if (reason === "zero_total") return "No stations are available, so a fast-share percentage cannot be calculated.";
  if (reason === "incomplete_classification") return "Some stations have unknown maximum point power, so the fast-share percentage is unavailable.";
  return "Fast, total, or maximum-point-power classification counts are unavailable.";
}

export function signedValue(value: number): string {
  return value > 0 ? `+${value.toFixed(1)}` : value.toFixed(1);
}

export type DisplayRow<T> = { value: number; nutsCode: string; item: T };

/** A07 order: signed deltas descend; district code only breaks display ties. */
export function sortDisplayRows<T>(rows: DisplayRow<T>[], isChange: boolean): DisplayRow<T>[] {
  return [...rows].sort((a, b) => {
    const primary = b.value - a.value;
    if (primary !== 0) return primary;
    return a.nutsCode.localeCompare(b.nutsCode);
  });
}

/** Baseline-only detail is retained outside Baseline mode, never replaced by a delta. */
export function detailContext<T extends NumericProperties>(current: T, baseline: T | undefined, useBaselineContext: boolean): T {
  return useBaselineContext && baseline ? baseline : current;
}

/** Accept the score-model shape used by either the snapshot manifest or WFS. */
export function publishedScoreModelTerms(payload: unknown): NumericProperties[] {
  const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : null;
  const direct = record?.score_model;
  if (Array.isArray(direct)) return direct.filter((term): term is NumericProperties => Boolean(term) && typeof term === "object");
  const features = record?.features;
  if (!Array.isArray(features)) return [];
  return features.map((feature) => feature && typeof feature === "object"
    ? (feature as { properties?: unknown }).properties
    : undefined).filter((term): term is NumericProperties => Boolean(term) && typeof term === "object");
}
