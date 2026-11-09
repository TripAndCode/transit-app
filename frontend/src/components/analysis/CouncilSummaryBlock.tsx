import { useTranslation } from "react-i18next";
import type { ReportResponse } from "../../api/types";
import { EM_DASH, fmtPct, formatDuration, formatMinutes, formatNumber } from "../../utils/format";
import { unrankedReason } from "../comparabilityReason";
import type { CouncilTile } from "../snapshotCompare";
import "./CouncilSummaryBlock.css";

const FOOTNOTE_MARK = /^(※|\* )/; // i18n-ignore: the server's footnote marks, not copy

type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;
type TFn = ReturnType<typeof useTranslation>["t"];
/** A headline tile; on a snapshot compared with today, `today` words
 *  today's value where its figures moved, and `side` names the one side a
 *  tile only one of them has. */
type Figure = { key: CouncilTile; label: string; value: string; note?: string; today?: string; side?: "snapshot" | "today" };

/** Today's council report for the same query, and the tiles whose figures
 *  moved (snapshotCompare.ts's changedCouncilTiles). */
type CouncilComparison = { today: CouncilReport; changed: ReadonlySet<CouncilTile> };

function count(v: number | null): string {
  return v == null ? EM_DASH : formatNumber(v);
}

/** Whether the response's scope narrowed the on-time figures by a condition
 *  its planned/delivered figures did not honour, so those read as the whole
 *  agency's rather than the filter's. */
function plannedIsWider({ ctx, scope_applied: applied, delivered_scope_applied: delivered }: CouncilReport): boolean {
  if (!ctx || !applied || !delivered) return false;
  const narrows: Record<string, boolean> = {
    dow: ctx.dow !== "all",
    time_band: ctx.time_band !== "all",
    service: (ctx.service ?? "all") !== "all",
    routes: (ctx.routes ?? []).length > 0,
  };
  return Object.keys(narrows).some((field) => narrows[field] && applied[field] && !delivered[field]);
}

/** The comparison tile: whether the agencies board would rank this agency,
 *  and otherwise the first check it fails, in the board's own words, read
 *  from the verdict's own figures only. Null for a response that carries no
 *  verdict. */
function comparisonFigure(report: CouncilReport, agencyWide: boolean, t: TFn): Figure | null {
  const gate = report.comparability;
  if (!gate) return null;
  const reason = unrankedReason({ ...gate, comparable_reason: gate.reason }, gate.thresholds, t);
  return {
    key: "comparison",
    label: agencyWide ? t("reports.council.comparison_agency_wide") : t("reports.council.comparison"),
    value: gate.comparable ? t("reports.council.comparable") : t("reports.council.not_comparable"),
    note: gate.comparable ? undefined : (reason ?? undefined),
  };
}

/** The headline figures as tiles, each keyed by what it shows. */
function councilFigures(report: CouncilReport, t: TFn): Figure[] {
  const [onTimePct, avgDelayMin, samples, plannedTrips, , deliveredPct, earlyPct] = report.rows[0];
  const earlySec = report.definition.early_departure_sec;
  const { covered_days: covered, window_days: days, delivered_missing: missing } = report;
  const agencyWide = plannedIsWider(report);
  const partlyPlanned = !!plannedTrips && covered != null && days != null && covered < days;
  const comparison = comparisonFigure(report, agencyWide, t);
  return [
    { key: "on_time", label: t("reports.council.on_time"), value: fmtPct(onTimePct, t) },
    ...(earlySec != null
      ? [{ key: "early" as const, label: t("reports.council.early_departures", { early: formatDuration(earlySec) }), value: fmtPct(earlyPct, t) }]
      : []),
    { key: "avg_delay", label: t("reports.council.avg_delay"), value: formatMinutes(avgDelayMin) },
    { key: "observations", label: t("reports.council.observations"), value: count(samples) },
    {
      key: "planned",
      label: agencyWide ? t("reports.council.planned_trips_agency_wide") : t("reports.council.planned_trips"),
      value: count(plannedTrips),
      note: partlyPlanned ? t("reports.council.plan_coverage", { covered, window: days }) : undefined,
    },
    {
      key: "delivered",
      label: agencyWide ? t("reports.council.delivered_agency_wide") : t("reports.council.delivered"),
      value: fmtPct(deliveredPct, t),
      note: deliveredPct == null && missing != null ? t(`network.delivered_missing.${missing}`) : undefined,
    },
    ...(comparison ? [comparison] : []),
  ];
}

/** Today's tiles beside the snapshot's: a moved tile carries today's value
 *  (and today's note where that differs), a tile only one side has says
 *  which, and today's extra tiles follow the snapshot's. */
function comparedFigures(own: Figure[], compare: CouncilComparison, t: TFn): Figure[] {
  const today = new Map(councilFigures(compare.today, t).map((f) => [f.key, f]));
  const keys = new Set(own.map((f) => f.key));
  return [
    ...own.map((f) => {
      const now = today.get(f.key);
      if (now == null) return { ...f, side: "snapshot" as const };
      if (!compare.changed.has(f.key)) return f;
      const note = now.note != null && now.note !== f.note ? now.note : null;
      const words = note == null ? t("snapshot.compare.today_value", { value: now.value }) : t("snapshot.compare.today_value_note", { value: now.value, note });
      return { ...f, today: words };
    }),
    ...[...today.values()].filter((f) => !keys.has(f.key)).map((f) => ({ ...f, side: "today" as const })),
  ];
}

/** The council report as a page someone can read aloud or print: the
 *  headline figures as tiles, the early-departure tile naming the threshold
 *  it counts past, then the server's prose -- its header line,
 *  the headline sentence and its footnotes -- then a print action. Planned
 *  trips say how many of the period's days a timetable plans, and a missing
 *  service-delivered rate says why, in the words the agencies board uses, as
 *  does the comparison tile for an agency the board would not rank. A
 *  snapshot compared with today marks each tile whose figures moved. */
export function CouncilSummaryBlock({ report, compare }: { report: CouncilReport; compare?: CouncilComparison }) {
  const { t } = useTranslation();
  const own = councilFigures(report, t);
  const figures = compare ? comparedFigures(own, compare, t) : own;
  const lines = report.text.split("\n").filter((line) => line.trim() !== "");
  const footnotes = lines.filter((line) => FOOTNOTE_MARK.test(line));
  const prose = lines.filter((line) => !FOOTNOTE_MARK.test(line));
  return (
    <div className="council-report">
      <ul className="council-report__figures" aria-label={t("reports.council.figures")}>
        {figures.map((f) => (
          <li key={f.key} className="council-report__figure">
            <span className="council-report__label">{f.label}</span>
            <span className="council-report__value">{f.value}</span>
            {f.note && <span className="council-report__note">{f.note}</span>}
            {f.today && <span className="figure-today council-report__mark">{f.today}</span>}
            {f.side && (
              <span className="caveat-badge council-report__mark">
                {t(f.side === "snapshot" ? "snapshot.compare.only_snapshot" : "snapshot.compare.only_today")}
              </span>
            )}
          </li>
        ))}
      </ul>
      {prose.map((line, i) => (
        <p key={i} className={i === 0 ? "council-report__lede" : undefined}>
          {line}
        </p>
      ))}
      {footnotes.map((line, i) => (
        <p key={`f${i}`} className="report-footnote">
          {line}
        </p>
      ))}
      <button type="button" className="btn-ghost council-report__print" onClick={() => window.print()}>
        {t("reports.print")}
      </button>
    </div>
  );
}
