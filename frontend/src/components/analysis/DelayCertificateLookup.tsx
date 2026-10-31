import { useState } from "react";
import { useTranslation } from "react-i18next";
import { REPORT_ROWS_MAX, useReport, type ReportOptions } from "../../api/hooks";
import { certificateDayScope } from "./certificateDayScope";
import type { DelayCertificateRow } from "../../api/types";
import { useRouteNames } from "../../api/useRouteNames";
import { ErrorBanner } from "../ErrorBanner";
import { StillWorking } from "../StillWorking";
import { RoutePickerPill } from "../paramPills/RoutePickerPill";
import { EM_DASH, formatDate, formatDuration } from "../../utils/format";
import "./DelayCertificateLookup.css";

/** Every departure that left late at all, so a passenger whose bus was a
 *  little late still finds it; the report's own default threshold is for
 *  staff reading the whole period. */
const LOOKUP_OPTIONS: ReportOptions = { thresholdSec: 0, limit: REPORT_ROWS_MAX };

/** A time without its seconds when they are :00. A timetable time almost
 *  always is; an actual time is the scheduled time shifted by the delay in
 *  seconds, so it shows them unless the delay is whole minutes. */
function clock(time: string): string {
  return /^\d+:\d\d:00$/.test(time) ? time.slice(0, -3) : time;
}

/** A departure's identity from its own values, so a refetch that adds or
 *  reorders rows never moves the choice onto another departure. Two rows
 *  sharing it would print the same certificate. */
function departureKey(row: DelayCertificateRow): string {
  return [row[1], row[3], row[4], row[5]].join("|");
}

/** A passenger's way to the delay record, in three steps: the day they
 *  travelled, up to the data's last day; their route; then their departure
 *  among that day's late ones. The record for it follows, ready to print.
 *  The day is held by the caller, so a list beside the lookup can follow it. */
export function DelayCertificateLookup({
  aid,
  date,
  latestDay,
  onDateChange,
}: {
  aid: number;
  date: string;
  latestDay: string;
  onDateChange: (date: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const routeNames = useRouteNames(aid);
  const [route, setRoute] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const ready = route != null;
  const departures = useReport(aid, ready ? "delay_certificate" : null, certificateDayScope(date, route), LOOKUP_OPTIONS);
  const rows =
    ready && !departures.isPlaceholderData && departures.data?.report_type === "delay_certificate"
      ? departures.data.rows
      : undefined;
  const row = chosen == null ? undefined : rows?.find((r) => departureKey(r) === chosen);
  // An empty list is "nothing left late" only when the feed reported the day;
  // without any observation, or when that couldn't be checked, it says so.
  const observed = departures.data?.observed;
  const emptyKey =
    observed === false
      ? "reports.certificate.no_data"
      : observed === null
        ? "reports.certificate.unconfirmed"
        : "reports.certificate.none";
  const noData = rows !== undefined && rows.length === 0 && observed === false;

  return (
    <div className="cert-lookup">
      <div className="cert-lookup__controls">
        <p className="cert-lookup__intro">{t("reports.certificate.intro")}</p>
        <ol className="cert-lookup__fields">
          <li className="cert-lookup__step-row">
            <span className="cert-lookup__step" aria-hidden="true">1</span>
            <label className="cert-lookup__field">
              {t("reports.certificate.date")}
              <input
                type="date"
                lang={i18n.language}
                value={date}
                max={latestDay}
                onChange={(e) => e.target.value && onDateChange(e.target.value > latestDay ? latestDay : e.target.value)}
              />
            </label>
          </li>
          <li className="cert-lookup__step-row">
            <span className="cert-lookup__step" aria-hidden="true">2</span>
            <div className="cert-lookup__field">
              <span aria-hidden="true">{t("reports.certificate.route")}</span>
              <RoutePickerPill
                label={t("reports.certificate.route")}
                value={route}
                agencyId={aid}
                placeholder={t("reports.certificate.route_placeholder")}
                onChange={setRoute}
              />
            </div>
          </li>
          {rows !== undefined && rows.length > 0 && (
            <li className="cert-lookup__step-row">
              <span className="cert-lookup__step" aria-hidden="true">3</span>
              <label className="cert-lookup__field">
                {t("reports.certificate.departure")}
                <select value={row ? departureKey(row) : ""} onChange={(e) => setChosen(e.target.value || null)}>
                  <option value="">{t("reports.certificate.departure_prompt")}</option>
                  {rows.map((r, i) => (
                    <option key={i} value={departureKey(r)}>
                      {r[7] != null
                        ? t("reports.certificate.departure_option_at", { time: clock(r[4]), stop: r[7], delay: formatDuration(r[6]) })
                        : t("reports.certificate.departure_option", { time: clock(r[4]), delay: formatDuration(r[6]) })}
                    </option>
                  ))}
                </select>
              </label>
            </li>
          )}
        </ol>
        {ready && departures.error && <ErrorBanner error={departures.error} onRetry={() => departures.refetch()} />}
        {ready && !departures.error && rows === undefined && (
          <>
            <p role="status" className="cert-lookup__note">
              {t("reports.certificate.loading")}
            </p>
            <StillWorking />
          </>
        )}
        {rows !== undefined && rows.length === 0 && (
          <p className="cert-lookup__note">{t(emptyKey)}</p>
        )}
        {rows !== undefined && !noData && <p className="cert-lookup__note">{t("reports.certificate.not_listed")}</p>}
      </div>
      {row && <CertificateCard row={row} routeName={routeNames.describe(row[1])} />}
    </div>
  );
}

/** Where the record's times were taken, said plainly: a feed often reports
 *  nothing for a trip's first stop, so the earliest stop it recorded is
 *  named as the trip's start only when the timetable starts the trip there. */
function stopNoteKey(stopName: string | null, stopIsFirst: boolean | null): string {
  if (stopName == null) return "reports.certificate.measured_at";
  return stopIsFirst ? "reports.certificate.stop_origin" : "reports.certificate.stop_later";
}

function CertificateCard({ row, routeName }: { row: DelayCertificateRow; routeName: string }) {
  const { t } = useTranslation();
  const [agencyName, , , date, scheduledTime, actualTime, delaySec, stopName, stopIsFirst] = row;
  const day = formatDate(date, { weekday: true });
  const scheduled = clock(scheduledTime);
  const actual = clock(actualTime);
  const delay = formatDuration(delaySec);
  const facts = [
    { label: t("reports.certificate.operator"), value: agencyName },
    { label: t("reports.certificate.route"), value: routeName },
    { label: t("reports.certificate.date"), value: day },
    { label: t("reports.certificate.stop"), value: stopName ?? EM_DASH },
    { label: t("reports.certificate.scheduled"), value: scheduled },
    { label: t("reports.certificate.actual"), value: actual },
    { label: t("reports.certificate.delay"), value: delay },
  ];
  return (
    <section className="cert-card" aria-label={t("reports.certificate.card")}>
      <p className="cert-card__statement">
        {stopName != null
          ? t("reports.certificate.statement_at", { scheduled, stop: stopName, delay, actual })
          : t("reports.certificate.statement", { scheduled, delay, actual })}
      </p>
      <dl className="cert-card__facts">
        {facts.map((f) => (
          <div key={f.label} className="cert-card__fact">
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="report-footnote">{t(stopNoteKey(stopName, stopIsFirst), { stop: stopName })}</p>
      <p className="cert-card__disclaimer">{t("reports.certificate.disclaimer", { operator: agencyName })}</p>
      <button type="button" className="btn-ghost cert-card__print" onClick={() => window.print()}>
        {t("reports.print")}
      </button>
    </section>
  );
}
