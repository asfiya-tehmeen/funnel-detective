import type { Filters } from "./types";

export const pct = (x: number | null | undefined, digits = 1): string =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(digits)}%`;

export const pp = (x: number | null | undefined, digits = 1): string => {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const v = Number(x.toFixed(digits));
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(digits)}pp`;
};

export const rel = (x: number | null | undefined): string => {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const v = Math.round(x);
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v)}%`;
};

export const int = (x: number | null | undefined): string =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : Math.round(x).toLocaleString("en-US");

export const pval = (p: number | null | undefined): string => {
  if (p === null || p === undefined) return "—";
  if (p < 0.001) return "<0.001";
  return p.toFixed(3);
};

export const segLabel = (seg: Record<string, string> | undefined): string =>
  seg && Object.keys(seg).length ? Object.values(seg).map(humanize).join(" · ") : "All traffic";

export function humanize(s: string): string {
  return s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function filtersLabel(f: Filters): string {
  const parts = Object.entries(f)
    .filter(([, v]) => v && v.length)
    .map(([, v]) => (v as string[]).map(humanize).join("/"));
  return parts.length ? parts.join(" · ") : "All traffic";
}

export function cleanFilters(f: Filters): Filters {
  const out: Filters = {};
  for (const [k, v] of Object.entries(f)) if (v && v.length) out[k as keyof Filters] = v;
  return out;
}

export const shortDate = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
};

export const STATUS_LABEL: Record<string, string> = {
  supported: "Supported",
  partially_supported: "Partially supported",
  not_supported: "Not supported",
  inconclusive: "Inconclusive",
};

export const CATEGORY_LABEL: Record<string, string> = {
  technical_regression: "Technical regression",
  traffic_composition: "Traffic composition",
  behavioral_change: "Behavioural change",
  instrumentation_issue: "Instrumentation issue",
  random_variation: "Random variation",
  selection_effect: "Selection effect",
};
