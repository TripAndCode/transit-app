import { REPORT_TYPE_IDS } from "../tabs/reportTypes";

/** The Analysis workspace's lenses. `predict` exists as a route id so saved
 *  links survive, but it isn't listed until its data ships. */
const LENS_IDS = ["overview", "when", "where", "why", "rider", "compare", "predict"] as const;
export type LensId = (typeof LENS_IDS)[number];
export const VISIBLE_LENSES: readonly LensId[] = ["overview", "when", "where", "why", "rider", "compare"];

/** Export-style report types live in Saved & export rather than a lens. */
export const SAVED_REPORT_TYPES = ["council_summary", "delay_certificate"] as const;

type ReportTypeId = (typeof REPORT_TYPE_IDS)[number];
const REPORT_LENS: Record<ReportTypeId, LensId | "saved"> = {
  ranking: "rider",
  ranking_best: "rider",
  on_time: "rider",
  worst_5min: "rider",
  trend: "when",
  compare_ranking: "compare",
  dow_weekday: "when",
  dow_weekend: "when",
  dwell_run: "why",
  route_forecast: "when",
  council_summary: "saved",
  delay_certificate: "saved",
};

export function isLensId(v: string | undefined): v is LensId {
  return v != null && (LENS_IDS as readonly string[]).includes(v);
}

export function reportDestination(reportType: string): LensId | "saved" {
  return REPORT_LENS[reportType as ReportTypeId] ?? "overview";
}

export function lensReportTypes(lens: LensId): readonly string[] {
  return REPORT_TYPE_IDS.filter((type) => REPORT_LENS[type] === lens);
}

export function mergeSearch(search: string, extra: Record<string, string>): string {
  const params = new URLSearchParams(search);
  for (const [k, v] of Object.entries(extra)) params.set(k, v);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function lensHref(
  agencyId: number | string,
  lens: LensId,
  search = "",
  extra: Record<string, string> = {},
): string {
  return `/agencies/${agencyId}/analysis/${lens}${mergeSearch(search, extra)}`;
}

export function reportHref(agencyId: number | string, reportType: string, search = ""): string {
  const dest = reportDestination(reportType);
  const qs = mergeSearch(search, { report: reportType });
  return dest === "saved" ? `/agencies/${agencyId}/saved${qs}` : `/agencies/${agencyId}/analysis/${dest}${qs}`;
}
