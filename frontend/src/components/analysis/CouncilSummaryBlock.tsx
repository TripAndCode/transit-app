import { useTranslation } from "react-i18next";
import type { ReportResponse } from "../../api/types";
import { EM_DASH, fmtPct, formatMinutes, formatNumber } from "../../utils/format";
import "./CouncilSummaryBlock.css";

const FOOTNOTE_MARK = /^(※|\* )/; // i18n-ignore: the server's footnote marks, not copy

type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;

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

/** The council report as a page someone can read aloud or print: the four
 *  headline figures as tiles, then the server's prose — its header line,
 *  the headline sentence and its footnotes — then a print action. */
export function CouncilSummaryBlock({ report }: { report: CouncilReport }) {
  const { t } = useTranslation();
  const [onTimePct, avgDelayMin, samples, plannedTrips] = report.rows[0];
  const figures = [
    { label: t("reports.council.on_time"), value: fmtPct(onTimePct, t) },
    { label: t("reports.council.avg_delay"), value: formatMinutes(avgDelayMin) },
    { label: t("reports.council.departures"), value: count(samples) },
    {
      label: plannedIsWider(report) ? t("reports.council.planned_trips_agency_wide") : t("reports.council.planned_trips"),
      value: count(plannedTrips),
    },
  ];
  const lines = report.text.split("\n").filter((line) => line.trim() !== "");
  const footnotes = lines.filter((line) => FOOTNOTE_MARK.test(line));
  const prose = lines.filter((line) => !FOOTNOTE_MARK.test(line));
  return (
    <div className="council-report">
      <ul className="council-report__figures" aria-label={t("reports.council.figures")}>
        {figures.map((f) => (
          <li key={f.label} className="council-report__figure">
            <span className="council-report__label">{f.label}</span>
            <span className="council-report__value">{f.value}</span>
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
