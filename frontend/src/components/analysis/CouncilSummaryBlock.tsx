import { useTranslation } from "react-i18next";
import type { CouncilSummaryRow } from "../../api/types";
import { fmtPct, formatMinutes, formatNumber } from "../../utils/format";
import "./CouncilSummaryBlock.css";

const FOOTNOTE_MARK = /^(※|\* )/; // i18n-ignore: the server's footnote marks, not copy

function count(v: number | null): string {
  return v == null ? "—" : formatNumber(v);
}

/** The council report as a page someone can read aloud or print: the four
 *  headline figures as tiles, then the server's prose — its header line,
 *  the headline sentence and its footnotes — then a print action. */
export function CouncilSummaryBlock({ row, text }: { row: CouncilSummaryRow; text: string }) {
  const { t } = useTranslation();
  const [onTimePct, avgDelayMin, samples, plannedTrips] = row;
  const figures = [
    { label: t("reports.council.on_time"), value: fmtPct(onTimePct, t) },
    { label: t("reports.council.avg_delay"), value: formatMinutes(avgDelayMin) },
    { label: t("reports.council.departures"), value: count(samples) },
    { label: t("reports.council.planned_trips"), value: count(plannedTrips) },
  ];
  const lines = text.split("\n").filter((line) => line.trim() !== "");
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
        {t("reports.council.print")}
      </button>
    </div>
  );
}
