import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { apiUrl } from "../../api/client";
import { useTimetableRevisionNewGaps } from "../../api/hooks";
import type { RevisionDayType, TimetableRevisionNewGaps } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SkeletonTable } from "../../components/Skeleton";
import { CsvLink } from "../../components/analysis/CsvLink";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { formatDuration, formatNumber } from "../../utils/format";
import { DEFAULT_BAND, DEFAULT_GAP_MIN, FIRST_HOUR, GAP_THRESHOLDS_MIN, LAST_HOUR, hourLabel } from "../supply/supplyModel";

/** How many stops the table lists before "show all". */
const TABLE_ROWS = 50;

const HOURS = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i);

const CONTROL_ROW: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 12, alignItems: "end" };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--text-sm)" };

/** The stops a revision left with a new gap on one day type: over a band of
 *  service-day hours, the longest stretch with no departure was at most the
 *  threshold before it and exceeds it after, the band and threshold read as
 *  the supply map reads them. */
export function RevisionNewGaps({ agencyId, on, dayType }: { agencyId: number; on: string; dayType: RevisionDayType }) {
  const { t } = useTranslation();
  const [fromHour, setFromHour] = useState<number>(DEFAULT_BAND.fromHour);
  const [toHour, setToHour] = useState<number>(DEFAULT_BAND.toHour);
  const [gapMin, setGapMin] = useState<number>(DEFAULT_GAP_MIN);
  // No gap exceeds the band, so a threshold the band does not exceed asks nothing.
  const problem = toHour <= fromHour ? "band_invalid" : gapMin >= (toHour - fromHour) * 60 ? "gap_too_long" : null;
  const query = useTimetableRevisionNewGaps(problem == null ? agencyId : null, on, dayType, { fromHour, toHour, gapMin });
  const band = { from_hour: String(fromHour), to_hour: String(toHour), gap_min: String(gapMin) };
  const csv = new URLSearchParams({ on, ...band, format: "csv" });
  return (
    <section>
      <h2>{t("revisions.new_gaps.title")}</h2>
      <div style={CONTROL_ROW}>
        <label style={FIELD}>
          {t("revisions.new_gaps.from")}
          <select value={fromHour} onChange={(e) => setFromHour(Number(e.target.value))}>
            {HOURS.slice(0, -1).map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("revisions.new_gaps.to")}
          <select value={toHour} onChange={(e) => setToHour(Number(e.target.value))}>
            {HOURS.slice(1).map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          {t("revisions.new_gaps.threshold")}
          <select value={gapMin} onChange={(e) => setGapMin(Number(e.target.value))}>
            {GAP_THRESHOLDS_MIN.map((m) => (
              <option key={m} value={m}>
                {t("revisions.new_gaps.minutes", { count: m })}
              </option>
            ))}
          </select>
        </label>
        {problem == null && <CsvLink href={apiUrl(`/api/${agencyId}/timetable-revisions/new-gaps?${csv.toString()}`)} />}
      </div>
      {problem != null ? (
        <p role="alert" className="focus-muted">
          {t(`revisions.new_gaps.${problem}`)}
        </p>
      ) : query.isError ? (
        <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <SkeletonTable />
      ) : (
        <div aria-busy={query.isPlaceholderData} style={{ opacity: query.isPlaceholderData ? 0.5 : 1 }}>
          <NewGapsBody key={`${on}:${dayType}`} data={query.data} dayType={dayType} />
        </div>
      )}
    </section>
  );
}

function NewGapsBody({ data, dayType }: { data: TimetableRevisionNewGaps; dayType: RevisionDayType }) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  if (!data.available) return null;
  const count = data.day_types.find((d) => d.day_type === dayType)?.stops;
  // An unknown day type is explained once, by the stop comparison above.
  if (count == null) return null;
  const lead = t("revisions.new_gaps.lead", {
    from: hourLabel(data.from_hour),
    to: hourLabel(data.to_hour),
    gap: data.gap_min,
  });
  if (count === 0) {
    return (
      <p className="focus-muted">
        {lead} {t("revisions.new_gaps.none")}
      </p>
    );
  }
  const shown = showAll ? data.stops : data.stops.slice(0, TABLE_ROWS);
  return (
    <>
      <p className="focus-muted">
        {lead} {t("revisions.new_gaps.count", { value: formatNumber(count) })}
      </p>
      <div className="focus-table-wrap">
        <table style={SHARED_TABLE}>
          <thead>
            <tr>
              <th style={th()}>{t("revisions.col.stop")}</th>
              <th style={th({ align: "right" })}>{t("revisions.new_gaps.col_gap")}</th>
              <th style={th({ align: "right" })}>{t("revisions.new_gaps.col_departures")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => (
              <tr key={s.stop_id}>
                <td style={td()}>{s.stop_name ?? s.stop_id}</td>
                <td style={td({ align: "right" })}>
                  {formatDuration(s.longest_gap_before)} → {formatDuration(s.longest_gap_after)}
                </td>
                <td style={td({ align: "right" })}>
                  {formatNumber(s.departures_before)} → {formatNumber(s.departures_after)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!showAll && data.stops.length > TABLE_ROWS && (
        <div className="focus-actions">
          <button type="button" onClick={() => setShowAll(true)}>
            {t("revisions.show_all", { count: data.stops.length })}
          </button>
        </div>
      )}
      <p className="focus-muted">{t("revisions.new_gaps.note")}</p>
    </>
  );
}
