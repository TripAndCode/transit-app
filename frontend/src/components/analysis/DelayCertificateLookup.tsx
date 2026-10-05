import { use, useState } from "react";
import { useTranslation } from "react-i18next";
import { REPORT_ROWS_MAX, useReport, type ReportOptions } from "../../api/hooks";
import { DataEndContext, SCOPE_EXTRAS_NONE, defaultPeriod, isoDaysAgo, type Scope } from "../../api/scope";
import type { DelayCertificateRow } from "../../api/types";
import { useRouteNames } from "../../api/useRouteNames";
import { ErrorBanner } from "../ErrorBanner";
import { RoutePickerPill } from "../paramPills/RoutePickerPill";
import { formatDate, formatDuration } from "../../utils/format";
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

/** A passenger's way to the delay certificate: the day and route they
 *  travelled on, then their departure among that day's late ones, then the
 *  certificate for it, ready to print. */
export function DelayCertificateLookup({ aid }: { aid: number }) {
  const { t, i18n } = useTranslation();
  const routeNames = useRouteNames(aid);
  const latestDay = defaultPeriod(use(DataEndContext)).to;
  const [pickedDate, setPickedDate] = useState<string | null>(null);
  const [route, setRoute] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const date = pickedDate ?? latestDay;
  const ready = route != null;
  const scope: Scope = {
    ...SCOPE_EXTRAS_NONE,
    from: date,
    to: date,
    dow: "all",
    time_band: "all",
    service: "all",
    routes: route != null ? [route] : [],
  };
  const departures = useReport(aid, ready ? "delay_certificate" : null, scope, LOOKUP_OPTIONS);
  const rows =
    ready && !departures.isPlaceholderData && departures.data?.report_type === "delay_certificate"
      ? departures.data.rows
      : undefined;
  const row = chosen == null ? undefined : rows?.find((r) => departureKey(r) === chosen);

  return (
    <div className="cert-lookup">
      <div className="cert-lookup__controls">
        <p className="cert-lookup__intro">{t("reports.certificate.intro")}</p>
        <div className="cert-lookup__fields">
          <label className="cert-lookup__field">
            {t("reports.certificate.date")}
            <input
              type="date"
              lang={i18n.language}
              value={date}
              max={isoDaysAgo(0)}
              onChange={(e) => e.target.value && setPickedDate(e.target.value)}
            />
          </label>
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
        </div>
        {ready && departures.error && <ErrorBanner error={departures.error} onRetry={() => departures.refetch()} />}
        {ready && !departures.error && rows === undefined && (
          <p role="status" className="cert-lookup__note">
            {t("reports.certificate.loading")}
          </p>
        )}
        {rows !== undefined && rows.length === 0 && <p className="cert-lookup__note">{t("reports.certificate.none")}</p>}
        {rows !== undefined && rows.length > 0 && (
          <label className="cert-lookup__field">
            {t("reports.certificate.departure")}
            <select value={row ? departureKey(row) : ""} onChange={(e) => setChosen(e.target.value || null)}>
              <option value="">{t("reports.certificate.departure_prompt")}</option>
              {rows.map((r, i) => (
                <option key={i} value={departureKey(r)}>
                  {t("reports.certificate.departure_option", { time: clock(r[4]), delay: formatDuration(r[6]) })}
                </option>
              ))}
            </select>
          </label>
        )}
        {rows !== undefined && <p className="cert-lookup__note">{t("reports.certificate.not_listed")}</p>}
      </div>
      {row && <CertificateCard row={row} routeName={routeNames.format(row[1])} />}
    </div>
  );
}

function CertificateCard({ row, routeName }: { row: DelayCertificateRow; routeName: string }) {
  const { t } = useTranslation();
  const [agencyName, , , date, scheduledTime, actualTime, delaySec] = row;
  const day = formatDate(date, { weekday: true });
  const scheduled = clock(scheduledTime);
  const actual = clock(actualTime);
  const delay = formatDuration(delaySec);
  const facts = [
    { label: t("reports.certificate.operator"), value: agencyName },
    { label: t("reports.certificate.route"), value: routeName },
    { label: t("reports.certificate.date"), value: day },
    { label: t("reports.certificate.scheduled"), value: scheduled },
    { label: t("reports.certificate.actual"), value: actual },
    { label: t("reports.certificate.delay"), value: delay },
  ];
  return (
    <section className="cert-card" aria-label={t("reports.certificate.card")}>
      <p className="cert-card__statement">
        {t("reports.certificate.statement", { date: day, scheduled, route: routeName, actual, delay })}
      </p>
      <dl className="cert-card__facts">
        {facts.map((f) => (
          <div key={f.label} className="cert-card__fact">
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="report-footnote">{t("reports.certificate.measured_at")}</p>
      <button type="button" className="btn-ghost cert-card__print" onClick={() => window.print()}>
        {t("reports.print")}
      </button>
    </section>
  );
}
