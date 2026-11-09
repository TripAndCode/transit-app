import { useTranslation } from "react-i18next";
import type { SnapshotSpec } from "../../api/snapshots";
import type { ReportResponse } from "../../api/types";
import { EM_DASH, fmtPct, formatDuration, formatMinutes, formatNumber } from "../../utils/format";
import { unrankedReason } from "../comparabilityReason";
import type { CouncilMark } from "../snapshotCompare";
import { CouncilPrint } from "./CouncilPrint";
import { councilTiles, gateFigures, type CouncilTile } from "./councilTiles";
import "./CouncilSummaryBlock.css";

const FOOTNOTE_MARK = /^(※|\* )/; // i18n-ignore: the server's footnote marks, not copy

type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;
type TFn = ReturnType<typeof useTranslation>["t"];
type Words = { label: string; value: string; note?: string };
/** A headline tile; on a snapshot compared with today, `today` words
 *  today's value where its figures moved, and `side` names the one side a
 *  tile only one of them has. */
type Figure = Words & { key: CouncilTile; today?: string; side?: "snapshot" | "today" };

/** Today's council report for the same query, and how each tile that reads
 *  differently stands (snapshotCompare.ts's compareCouncilTiles). */
type CouncilComparison = { today: CouncilReport; marks: ReadonlyMap<CouncilTile, CouncilMark> };

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

/** One tile in words. The comparison tile says whether the agencies board
 *  would rank this agency, and otherwise the first check it fails, in the
 *  board's own words, read from the verdict's own figures only; it has no
 *  words for a response that carries no verdict. */
function tileWords(key: CouncilTile, report: CouncilReport, t: TFn): Words | null {
  const [onTimePct, avgDelayMin, samples, plannedTrips, , deliveredPct, earlyPct] = report.rows[0];
  const { covered_days: covered, window_days: days, delivered_missing: missing } = report;
  const agencyWide = plannedIsWider(report);
  switch (key) {
    case "on_time":
      return { label: t("reports.council.on_time"), value: fmtPct(onTimePct, t) };
    case "early":
      return {
        label: t("reports.council.early_departures", { early: formatDuration(report.definition.early_departure_sec) }),
        value: fmtPct(earlyPct, t),
      };
    case "avg_delay":
      return { label: t("reports.council.avg_delay"), value: formatMinutes(avgDelayMin) };
    case "observations":
      return { label: t("reports.council.observations"), value: count(samples) };
    case "planned": {
      const partlyPlanned = !!plannedTrips && covered != null && days != null && covered < days;
      return {
        label: agencyWide ? t("reports.council.planned_trips_agency_wide") : t("reports.council.planned_trips"),
        value: count(plannedTrips),
        note: partlyPlanned ? t("reports.council.plan_coverage", { covered, window: days }) : undefined,
      };
    }
    case "delivered":
      return {
        label: agencyWide ? t("reports.council.delivered_agency_wide") : t("reports.council.delivered"),
        value: fmtPct(deliveredPct, t),
        note: deliveredPct == null && missing != null ? t(`network.delivered_missing.${missing}`) : undefined,
      };
    case "comparison": {
      const gate = report.comparability;
      if (!gate) return null;
      const reason = unrankedReason(gateFigures(gate), gate.thresholds, t);
      return {
        label: agencyWide ? t("reports.council.comparison_agency_wide") : t("reports.council.comparison"),
        value: gate.comparable ? t("reports.council.comparable") : t("reports.council.not_comparable"),
        note: gate.comparable ? undefined : (reason ?? undefined),
      };
    }
  }
}

/** The tiles the report shows (councilTiles), in words. */
function councilFigures(report: CouncilReport, t: TFn): Figure[] {
  return councilTiles(report).flatMap(({ key }) => {
    const words = tileWords(key, report, t);
    return words ? [{ key, ...words }] : [];
  });
}

/** The snapshot's tiles marked against today's: a tile that reads
 *  differently carries today's value (and today's note where that differs),
 *  a tile only the snapshot has says so, and the tiles only today has follow
 *  the snapshot's. */
function comparedFigures(own: Figure[], compare: CouncilComparison, t: TFn): Figure[] {
  const marked = own.map((f): Figure => {
    const mark = compare.marks.get(f.key);
    if (mark === "snapshot") return { ...f, side: "snapshot" };
    const now = mark === "changed" ? tileWords(f.key, compare.today, t) : null;
    if (now == null) return f;
    const note = now.note != null && now.note !== f.note ? now.note : null;
    const words = note == null ? t("snapshot.compare.today_value", { value: now.value }) : t("snapshot.compare.today_value_note", { value: now.value, note });
    return { ...f, today: words };
  });
  const added = [...compare.marks].flatMap(([key, mark]): Figure[] => {
    const words = mark === "today" ? tileWords(key, compare.today, t) : null;
    return words ? [{ key, ...words, side: "today" }] : [];
  });
  return [...marked, ...added];
}

/** The council report as a page someone can read aloud or print: the
 *  headline figures as tiles, the early-departure tile naming the threshold
 *  it counts past, then the server's prose -- its header line,
 *  the headline sentence and its footnotes -- then a print action. Planned
 *  trips say how many of the period's days a timetable plans, and a missing
 *  service-delivered rate says why, in the words the agencies board uses, as
 *  does the comparison tile for an agency the board would not rank. A
 *  snapshot compared with today marks each tile whose figures moved. Given
 *  `snapshot`, the live screen's query, its print carries a snapshot of the
 *  figures (CouncilPrint). */
export function CouncilSummaryBlock({
  report,
  compare,
  snapshot,
}: {
  report: CouncilReport;
  compare?: CouncilComparison;
  snapshot?: SnapshotSpec | null;
}) {
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
      <CouncilPrint report={report} snapshot={snapshot} />
    </div>
  );
}
