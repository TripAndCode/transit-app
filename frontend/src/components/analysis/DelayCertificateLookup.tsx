import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useDelayRecordStops, useDelayRecordTrips } from "../../api/hooks";
import type { DelayRecordStop, DelayRecordTrip } from "../../api/types";
import { useRouteNames } from "../../api/useRouteNames";
import { ErrorBanner } from "../ErrorBanner";
import { StillWorking } from "../StillWorking";
import { RoutePickerPill } from "../paramPills/RoutePickerPill";
import { EM_DASH, formatDate, formatDuration } from "../../utils/format";
import "./DelayCertificateLookup.css";

const DAY_SEC = 24 * 3600;

const pad = (n: number) => String(n).padStart(2, "0");

/** A service-day time as a passenger reads it: past 24h it is the next
 *  calendar day's clock, and its seconds show only when they are not :00
 *  (a timetable time almost never has them; a time shifted by a delay in
 *  seconds can). */
function clock(t: TFunction, sec: number | null): string {
  if (sec == null) return EM_DASH;
  const within = ((sec % DAY_SEC) + DAY_SEC) % DAY_SEC;
  const hhmm = `${pad(Math.floor(within / 3600))}:${pad(Math.floor((within % 3600) / 60))}`;
  const time = within % 60 ? `${hhmm}:${pad(within % 60)}` : hhmm;
  return sec >= DAY_SEC ? t("reports.certificate.next_day", { time }) : time;
}

/** A trip as step 3 lists it. Only a trip the day's timetable runs is named
 *  by its start and where it runs to, with the feed's headsign beside it;
 *  any other is named by its earliest recorded time, never called its start. */
function tripLabel(t: TFunction, trip: DelayRecordTrip): string {
  const time = clock(t, trip.departure_sec);
  if (!trip.matched) return t("reports.certificate.trip_option_unmatched", { time });
  const label = trip.terminal
    ? t("reports.certificate.trip_option", { time, terminal: trip.terminal })
    : t("reports.certificate.trip_option_time", { time });
  return trip.headsign ? t("reports.certificate.trip_option_note", { label, note: trip.headsign }) : label;
}

/** The trip as the record's sentence names it: by its scheduled departure
 *  from the chosen stop, the time a passenger waiting there knows it by, so
 *  the sentence's own figures add up (17:42 + 8 min = 17:50). */
function tripPhrase(t: TFunction, stop: DelayRecordStop, position: "subject" | "object"): string {
  return stop.scheduled_sec != null
    ? t(`reports.certificate.trip_${position}`, { time: clock(t, stop.scheduled_sec) })
    : t(`reports.certificate.trip_${position}_untimed`);
}

function stopLabel(t: TFunction, stop: DelayRecordStop): string {
  return stop.stop_name ?? t("reports.certificate.stop_unmatched");
}

/** How far off its schedule a departure left, in words. */
function delayPhrase(t: TFunction, delaySec: number): string {
  if (delaySec > 0) return t("reports.certificate.delay_late", { delay: formatDuration(delaySec) });
  if (delaySec < 0) return t("reports.certificate.delay_early", { delay: formatDuration(-delaySec) });
  return t("reports.certificate.delay_on_time");
}

/** The stops a passenger can have boarded at: a trip's last stop is where
 *  it ends, and a feed reports only an arrival there, so it is offered only
 *  when it carries a departure reading. */
function boardingStops(stops: DelayRecordStop[]): DelayRecordStop[] {
  const last = stops.at(-1);
  return last && last.dep_delay == null ? stops.slice(0, -1) : stops;
}

type TripChoice = { date: string; route: string; trip: string };
type StopChoice = TripChoice & { stop: number };

/** A passenger's way to the delay record, in four steps: the day they
 *  travelled, up to the data's last day; their route; their trip among
 *  every trip the route ran that day; then the stop they boarded at. The
 *  record gives the departure delay at that stop, ready to print. The day is
 *  held by the caller, so a list beside the lookup can follow it. */
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
  // A trip_id recurs every day the trip runs, so each choice keeps the day
  // and route it was made for and lapses once either changes.
  const [tripChoice, setTripChoice] = useState<TripChoice | null>(null);
  const [stopChoice, setStopChoice] = useState<StopChoice | null>(null);
  const trip = tripChoice?.date === date && tripChoice.route === route ? tripChoice.trip : null;
  const stopSeq =
    trip != null && stopChoice?.date === date && stopChoice.route === route && stopChoice.trip === trip
      ? stopChoice.stop
      : null;

  const trips = useDelayRecordTrips(aid, date, route);
  const stops = useDelayRecordStops(aid, date, route, trip);
  const tripList = route != null ? trips.data?.trips : undefined;
  const record = trip != null ? stops.data : undefined;
  const offered = record?.trip ? boardingStops(record.stops) : [];
  const stopIndex = record && stopSeq != null ? record.stops.findIndex((s) => s.stop_sequence === stopSeq) : -1;

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
          {route != null && tripList !== undefined && tripList.length > 0 && (
            <li className="cert-lookup__step-row">
              <span className="cert-lookup__step" aria-hidden="true">3</span>
              <label className="cert-lookup__field">
                {t("reports.certificate.trip")}
                <select
                  value={trip ?? ""}
                  onChange={(e) => setTripChoice(e.target.value ? { date, route, trip: e.target.value } : null)}
                >
                  <option value="">{t("reports.certificate.trip_prompt")}</option>
                  {tripList.map((tr) => (
                    <option key={tr.trip_id} value={tr.trip_id}>
                      {tripLabel(t, tr)}
                    </option>
                  ))}
                </select>
              </label>
            </li>
          )}
          {route != null && trip != null && offered.length > 0 && (
            <li className="cert-lookup__step-row">
              <span className="cert-lookup__step" aria-hidden="true">4</span>
              <label className="cert-lookup__field">
                {t("reports.certificate.boarding_stop")}
                <select
                  value={stopSeq ?? ""}
                  onChange={(e) =>
                    setStopChoice(e.target.value ? { date, route, trip, stop: Number(e.target.value) } : null)
                  }
                >
                  <option value="">{t("reports.certificate.stop_prompt")}</option>
                  {offered.map((s) => (
                    <option key={s.stop_sequence} value={s.stop_sequence}>
                      {t(s.dep_delay != null ? "reports.certificate.stop_option_recorded" : "reports.certificate.stop_option", {
                        time: clock(t, s.scheduled_sec),
                        stop: stopLabel(t, s),
                      })}
                    </option>
                  ))}
                </select>
              </label>
            </li>
          )}
        </ol>
        {route != null && trips.error && <ErrorBanner error={trips.error} onRetry={() => trips.refetch()} />}
        {route != null && !trips.error && tripList === undefined && (
          <>
            <p role="status" className="cert-lookup__note">
              {t("reports.certificate.loading_trips")}
            </p>
            <StillWorking />
          </>
        )}
        {tripList !== undefined && (
          <p className="cert-lookup__note">
            {t(tripList.length === 0 ? "reports.certificate.no_trips" : "reports.certificate.not_listed")}
          </p>
        )}
        {trip != null && stops.error && <ErrorBanner error={stops.error} onRetry={() => stops.refetch()} />}
        {trip != null && !stops.error && record === undefined && (
          <p role="status" className="cert-lookup__note">
            {t("reports.certificate.loading_stops")}
          </p>
        )}
        {record !== undefined && record.trip == null && (
          <p className="cert-lookup__note">{t("reports.certificate.no_stops")}</p>
        )}
      </div>
      {record?.trip && stopIndex >= 0 && (
        <RecordCard
          agencyName={record.agency_name}
          routeName={routeNames.describe(record.route)}
          date={record.date}
          trip={record.trip}
          stops={record.stops}
          index={stopIndex}
        />
      )}
    </div>
  );
}

/** The record at one stop: the departure delay its reading gives, or, with
 *  no reading, that there is none, beside the nearest recorded stops either
 *  side so the passenger can judge for themselves. Never a value the feed
 *  did not report. */
function RecordCard({
  agencyName,
  routeName,
  date,
  trip,
  stops,
  index,
}: {
  agencyName: string;
  routeName: string;
  date: string;
  trip: DelayRecordTrip;
  stops: DelayRecordStop[];
  index: number;
}) {
  const { t } = useTranslation();
  const stop = stops[index];
  const name = stopLabel(t, stop);
  const delay = stop.dep_delay;
  const actualSec = delay != null && stop.scheduled_sec != null ? stop.scheduled_sec + delay : null;
  const statementKey =
    delay == null
      ? "reports.certificate.statement_none"
      : delay > 0
        ? "reports.certificate.statement_late"
        : delay < 0
          ? "reports.certificate.statement_early"
          : "reports.certificate.statement_on_time";
  const statement = t(statementKey, {
    trip: tripPhrase(t, stop, delay == null ? "object" : "subject"),
    stop: name,
    delay: delay == null ? "" : formatDuration(Math.abs(delay)),
    actual: clock(t, actualSec),
  });
  const before = delay == null ? stops.slice(0, index).reverse().find((s) => s.dep_delay != null) : undefined;
  const after = delay == null ? stops.slice(index + 1).find((s) => s.dep_delay != null) : undefined;
  const facts = [
    { label: t("reports.certificate.operator"), value: agencyName },
    { label: t("reports.certificate.route"), value: routeName },
    { label: t("reports.certificate.date"), value: formatDate(date, { weekday: true }) },
    { label: t("reports.certificate.trip"), value: tripLabel(t, trip) },
    { label: t("reports.certificate.stop"), value: name },
    { label: t("reports.certificate.scheduled"), value: clock(t, stop.scheduled_sec) },
    { label: t("reports.certificate.actual"), value: clock(t, actualSec) },
    { label: t("reports.certificate.delay"), value: delay == null ? EM_DASH : delayPhrase(t, delay) },
  ];
  return (
    <section className="cert-card" aria-label={t("reports.certificate.card")}>
      <p className="cert-card__statement">{statement}</p>
      {(before || after) && (
        <ul className="cert-card__nearby">
          {before && (
            <li>{t("reports.certificate.nearest_before", { stop: stopLabel(t, before), delay: delayPhrase(t, before.dep_delay!) })}</li>
          )}
          {after && (
            <li>{t("reports.certificate.nearest_after", { stop: stopLabel(t, after), delay: delayPhrase(t, after.dep_delay!) })}</li>
          )}
        </ul>
      )}
      <dl className="cert-card__facts">
        {facts.map((f) => (
          <div key={f.label} className="cert-card__fact">
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="report-footnote">{t("reports.certificate.reading_note")}</p>
      {!trip.matched && <p className="report-footnote">{t("reports.certificate.unmatched_note")}</p>}
      <p className="cert-card__disclaimer">{t("reports.certificate.disclaimer", { operator: agencyName })}</p>
      <button type="button" className="btn-ghost cert-card__print" onClick={() => window.print()}>
        {t("reports.print")}
      </button>
    </section>
  );
}
