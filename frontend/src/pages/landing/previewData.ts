// Static, deterministic mock data for the pre-login dashboard-preview shell
// (`DashboardPreview` and its panels). None of this is fetched -- the
// preview renders before sign-in, with no agency/session context to query
// real aggregates for, so every figure here is illustrative example data,
// not a snapshot of anything real. Kept in one module so every panel reads
// from the same fixed numbers instead of each inventing its own.

export type PreviewAgencyKey = "riverside" | "harborline" | "hillcrest";

export type PreviewAgency = {
  key: PreviewAgencyKey;
  /** i18n key for the display name -- kept as translated copy (not a raw
   *  string in source) purely for consistency with every other user-facing
   *  label on this page, even though the name itself is invented. */
  nameKey: string;
  avgDelayMin: number;
  onTimePct: number;
};

export const PREVIEW_AGENCIES: PreviewAgency[] = [
  { key: "riverside", nameKey: "landing.preview.agency.riverside", avgDelayMin: 1.6, onTimePct: 92 },
  { key: "harborline", nameKey: "landing.preview.agency.harborline", avgDelayMin: 2.4, onTimePct: 85 },
  { key: "hillcrest", nameKey: "landing.preview.agency.hillcrest", avgDelayMin: 3.9, onTimePct: 78 },
];

export type PreviewRoute = { code: string; delayMin: number; onTime: boolean };

// One route pair per agency (on-time/at-risk) so the Overview panel's filter
// chips ("all" / "on-time" / "delayed") always have at least one match in
// either sub-filter, whichever agency is selected.
export const PREVIEW_ROUTES: Record<PreviewAgencyKey, PreviewRoute[]> = {
  riverside: [
    { code: "R1", delayMin: 0.6, onTime: true },
    { code: "R4", delayMin: 1.2, onTime: true },
    { code: "R7", delayMin: 3.8, onTime: false },
  ],
  harborline: [
    { code: "H2", delayMin: 1.0, onTime: true },
    { code: "H5", delayMin: 2.9, onTime: false },
    { code: "H9", delayMin: 4.4, onTime: false },
  ],
  hillcrest: [
    { code: "C3", delayMin: 1.4, onTime: true },
    { code: "C6", delayMin: 3.3, onTime: false },
    { code: "C8", delayMin: 5.1, onTime: false },
  ],
};

// Average delay (minutes) by day of week, Mon-Sun -- paired with the
// existing `forecast.dow_*` translation keys so the trend view needs no new
// day-label strings.
export const PREVIEW_TREND_BY_DOW: readonly number[] = [1.1, 1.4, 2.0, 1.8, 2.6, 0.9, 0.7];
export const DOW_KEYS = [
  "forecast.dow_mon",
  "forecast.dow_tue",
  "forecast.dow_wed",
  "forecast.dow_thu",
  "forecast.dow_fri",
  "forecast.dow_sat",
  "forecast.dow_sun",
] as const;

// A handful of representative hours (not all 24) -- enough to read as a
// day's shape at a glance without the panel needing a real scrubber.
export const PREVIEW_HOURLY = [
  { hour: 6, delayMin: 0.8 },
  { hour: 9, delayMin: 2.1 },
  { hour: 12, delayMin: 1.5 },
  { hour: 15, delayMin: 3.4 },
  { hour: 18, delayMin: 4.2 },
  { hour: 21, delayMin: 1.0 },
] as const;

export type PreviewObservation = {
  routeCode: string;
  stopN: number;
  delayMin: number;
  minutesAgo: number;
};

// Latest-observations mock feed, deliberately unsorted by either field so
// both sort modes visibly reorder the list.
export const PREVIEW_OBSERVATIONS: PreviewObservation[] = [
  { routeCode: "R1", stopN: 4, delayMin: 0.5, minutesAgo: 2 },
  { routeCode: "R4", stopN: 12, delayMin: 3.1, minutesAgo: 6 },
  { routeCode: "R7", stopN: 2, delayMin: 0.2, minutesAgo: 1 },
  { routeCode: "R9", stopN: 9, delayMin: 5.0, minutesAgo: 9 },
  { routeCode: "R1", stopN: 7, delayMin: 1.1, minutesAgo: 14 },
];

export type PreviewAskExchange = { questionKey: string; answerKey: string };

// Canned question/answer pairs for the Ask preview's suggestion chips. Real
// Ask routing (rules -> embedding nearest-neighbour -> RAG LLM, per
// CLAUDE.md) needs a signed-in session and a real agency; this preview
// exists specifically so a prospective user can see the *shape* of an Ask
// conversation before either of those exist.
export const PREVIEW_ASK_EXCHANGES: PreviewAskExchange[] = [
  { questionKey: "landing.preview.ask.q1", answerKey: "landing.preview.ask.a1" },
  { questionKey: "landing.preview.ask.q2", answerKey: "landing.preview.ask.a2" },
  { questionKey: "landing.preview.ask.q3", answerKey: "landing.preview.ask.a3" },
];
