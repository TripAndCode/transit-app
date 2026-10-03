import type { TFunction } from "i18next";

/** The report list's reading order: what ranks, what measures punctuality,
 *  what describes a pattern over time, and what is produced to hand to
 *  someone else. Thirteen identically-shaped buttons gave a reader no way to
 *  tell a ranking from a certificate; the grouping is the difference.
 *
 *  `route_forecast` is listed here like any other type. The reports API does
 *  not return it (it is served by its own endpoint), so it used to be a
 *  second, hand-copied button underneath the mapped list -- data now, not a
 *  special case in the markup. */
export const REPORT_GROUPS = {
  ranking: ["ranking", "ranking_best", "compare_ranking"],
  punctuality: ["on_time", "worst_5min"],
  pattern: ["trend", "dow_weekday", "dow_weekend", "dwell_run"],
  forecast: ["route_forecast", "council_summary", "delay_certificate"],
} as const satisfies Record<string, readonly string[]>;

type ReportGroupKey = keyof typeof REPORT_GROUPS | "other";

type ReportGroup = {
  key: ReportGroupKey;
  types: string[];
};

/** Groups the available report types, keeping each group's own order rather
 *  than the order the API happened to list them in. A type this build does
 *  not know about is carried through under `other` -- a new server-side
 *  report must still be reachable, even before the client learns where it
 *  belongs. Empty groups are dropped. */
export function groupReports(available: readonly string[]): ReportGroup[] {
  const remaining = new Set(available);
  const groups: ReportGroup[] = [];
  for (const [key, members] of Object.entries(REPORT_GROUPS)) {
    const types = members.filter((type) => remaining.has(type));
    for (const type of types) remaining.delete(type);
    if (types.length > 0) groups.push({ key: key as ReportGroupKey, types });
  }
  const unknown = available.filter((type) => remaining.has(type));
  if (unknown.length > 0) groups.push({ key: "other", types: unknown });
  return groups;
}

/** The report's display name, falling back to the raw identifier so an
 *  unrecognised type is still legible rather than blank. */
export function reportLabel(t: TFunction, type: string): string {
  return t(`reports.type.${type}`, { defaultValue: type });
}

export function reportDescriptionKey(type: string): string {
  return `reports.desc.${type}`;
}
