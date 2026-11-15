// Static fixture data for the whole /welcome page. Every figure, table row,
// chart and map the page draws comes from here, never from an API call -- a
// signed-out visitor sees no real data. Values are plain ASCII (numbers and
// key suffixes only, no human-readable text): scripts/lint-i18n-strings.py
// bans literal Japanese/English copy in source files, and every label a
// reader sees still goes through t().
import type { DelayBand } from "../../styles/tokens";
import type { FixtureBand } from "./severityStyle";

export type Band = DelayBand;

export const ON_TIME_THRESHOLD_SECONDS = 90;

export const STATUS = {
  updatedAt: "11/13 14:32",
  feeds: 38,
  cities: 31,
  busesTracked: 9412,
  onTimePct: 82.6,
} as const;

export interface StageCity {
  readonly key: string;
  /** Percent position within the stage's viewBox. */
  readonly x: number;
  readonly y: number;
  readonly band: FixtureBand;
  /** i18n label suffix (under landing.stage.cities.*) for the few cities the
   *  stage labels in HTML; absent for the purely decorative dots. */
  readonly labelKey?: string;
  readonly labelSide?: "left" | "right" | "below";
}

// Mirrors the mockup's 26-point dot map. Only the 7 labelled cities carry a
// labelKey; the rest are decorative (aria-hidden) dots that still vary the
// stage's color field the way the mockup's full map does.
export const STAGE_CITIES: readonly StageCity[] = [
  { key: "sapporo", x: 74.57, y: 19.2, band: "ok", labelKey: "sapporo", labelSide: "right" },
  { key: "obihiro", x: 82.81, y: 20.08, band: "ok" },
  { key: "hakodate", x: 71.81, y: 27.38, band: "mild" },
  { key: "aomori", x: 71.85, y: 33.41, band: "ok", labelKey: "aomori", labelSide: "right" },
  { key: "morioka", x: 73.68, y: 40.52, band: "ok" },
  { key: "sendai", x: 72.43, y: 49.59, band: "mild", labelKey: "sendai", labelSide: "right" },
  { key: "niigata", x: 64.19, y: 51.82, band: "ok" },
  { key: "maebashi", x: 64.37, y: 61.52, band: "moderate" },
  { key: "utsunomiya", x: 68.02, y: 60.45, band: "mild" },
  { key: "tokyo", x: 67.18, y: 65.97, band: "moderate" },
  { key: "yokohama", x: 66.95, y: 67.55, band: "mild" },
  { key: "shizuoka", x: 61.34, y: 70.47, band: "ok" },
  { key: "toyama", x: 56.13, y: 59.56, band: "ok" },
  { key: "kanazawa", x: 53.63, y: 60.45, band: "mild" },
  { key: "gifu", x: 54.12, y: 67.68, band: "mild" },
  { key: "nagoya", x: 54.79, y: 69.2, band: "mild", labelKey: "nagoya", labelSide: "below" },
  { key: "kyoto", x: 49.71, y: 70.28, band: "moderate" },
  { key: "kobe", x: 47.13, y: 72.31, band: "ok" },
  { key: "okayama", x: 41.47, y: 72.5, band: "ok" },
  { key: "hiroshima", x: 34.97, y: 74.15, band: "moderate", labelKey: "hiroshima", labelSide: "left" },
  { key: "matsuyama", x: 36.35, y: 77.71, band: "moderate" },
  { key: "kochi", x: 39.73, y: 79.48, band: "ok" },
  { key: "fukuoka", x: 25.79, y: 79.29, band: "mild", labelKey: "fukuoka", labelSide: "left" },
  { key: "kumamoto", x: 27.3, y: 84.37, band: "severe" },
  { key: "kagoshima", x: 26.5, y: 91.92, band: "ok" },
  { key: "naha", x: 10.9, y: 38.9, band: "offline", labelKey: "naha", labelSide: "right" },
] as const;

export interface FeedRow {
  readonly cityKey: string;
  readonly feeds: number;
  /** Null when the feed is offline and has no fresh observation to report. */
  readonly freshnessSeconds: number | null;
  readonly freshnessBand: Band;
  readonly onTimePct: number | null;
  readonly grade: "A" | "B" | "C" | "D" | "F";
  readonly gradeBand: Band;
  readonly coveragePct: number;
  readonly lastUpdateSeconds: number;
}

export const FEED_ROWS: readonly FeedRow[] = [
  { cityKey: "aomori", feeds: 1, freshnessSeconds: 14, freshnessBand: "ok", onTimePct: 88.4, grade: "A", gradeBand: "ok", coveragePct: 97, lastUpdateSeconds: 12 },
  { cityKey: "sendai", feeds: 2, freshnessSeconds: 22, freshnessBand: "ok", onTimePct: 83.1, grade: "B", gradeBand: "ok", coveragePct: 93, lastUpdateSeconds: 31 },
  { cityKey: "toyama", feeds: 1, freshnessSeconds: 19, freshnessBand: "ok", onTimePct: 90.2, grade: "A", gradeBand: "ok", coveragePct: 98, lastUpdateSeconds: 18 },
  { cityKey: "gifu", feeds: 2, freshnessSeconds: 112, freshnessBand: "mild", onTimePct: 76.8, grade: "C", gradeBand: "mild", coveragePct: 86, lastUpdateSeconds: 120 },
  { cityKey: "kyoto", feeds: 2, freshnessSeconds: 26, freshnessBand: "ok", onTimePct: 71.5, grade: "A", gradeBand: "ok", coveragePct: 96, lastUpdateSeconds: 25 },
  { cityKey: "hiroshima", feeds: 4, freshnessSeconds: 33, freshnessBand: "ok", onTimePct: 74.2, grade: "B", gradeBand: "ok", coveragePct: 91, lastUpdateSeconds: 40 },
  { cityKey: "matsuyama", feeds: 1, freshnessSeconds: 365, freshnessBand: "moderate", onTimePct: 69.0, grade: "D", gradeBand: "moderate", coveragePct: 72, lastUpdateSeconds: 360 },
  { cityKey: "naha", feeds: 3, freshnessSeconds: null, freshnessBand: "severe", onTimePct: null, grade: "F", gradeBand: "severe", coveragePct: 41, lastUpdateSeconds: 2820 },
];

/** Percentage-based on-time band, independent of the minutes-based
 *  `delayBand()`: these thresholds classify a feed's on-time *rate*, not a
 *  single trip's lateness. */
export function onTimeRateBand(pct: number): Band {
  if (pct >= 85) return "ok";
  if (pct >= 75) return "mild";
  if (pct >= 65) return "moderate";
  return "severe";
}

export const REPORT = {
  fiscalYear: 2026,
  quarter: 2,
  fromMonth: 7,
  fromDay: 1,
  toMonth: 9,
  toDay: 30,
  routeCount: 5,
  confirmationPct: 98.6,
  confirmedTrips: 12660,
  totalTrips: 12840,
  onTimePct: 81.2,
  coveragePct: 94.3,
  targetPct: 80,
  unobservedTrips: 732,
  timetableYear: 2026,
  timetableMonth: 7,
  timetableDay: 1,
  definitionVersion: 5,
  generatedDate: "2026-10-02",
  recalculationId: "q2-0412",
  page: 1,
  pageTotal: 6,
} as const;

export interface ReportRoute {
  readonly nameKey: string;
  readonly pct: number;
  readonly belowTarget: boolean;
}

export const REPORT_ROUTES: readonly ReportRoute[] = [
  { nameKey: "loop", pct: 88.1, belowTarget: false },
  { nameKey: "north", pct: 84.6, belowTarget: false },
  { nameKey: "eastwest", pct: 80.9, belowTarget: false },
  { nameKey: "south", pct: 76.3, belowTarget: true },
  { nameKey: "mountain", pct: 68.7, belowTarget: true },
];

export const COLLECT_TICKS: readonly { readonly heightPct: number; readonly gap: boolean }[] = Array.from(
  { length: 24 },
  (_, i) => {
    const gap = i === 14 || i === 15;
    return { heightPct: gap ? 100 : 55 + ((i * 37) % 45), gap };
  },
);
export const COLLECT_GAP_MINUTES = 2;

export const COMPARE_SAMPLE = {
  scheduled: "08:12:00",
  observed: "08:14:30",
  deltaMinutes: 2,
  deltaSeconds: 30,
};

export const PUBLISH_SAMPLE = {
  definitionVersion: 5,
  revisedDate: "2026-10-20",
};

export const HONESTY_UNOBSERVED_STOPS = 212;

export const BUS_TOWN_SAMPLE = {
  cityKey: "hiroshima",
  date: "2026-11-02",
};
