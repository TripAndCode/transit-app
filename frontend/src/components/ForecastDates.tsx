/**
 * Which days the route forecast stands for. Its grid mixes two kinds of
 * figure: a forecast-model cell is for one coming date, and every other cell
 * is an average over the analyzed history, a typical week rather than any
 * date. These pieces name both, so no figure on the page leaves a reader
 * guessing whether it is for a coming day or for a typical week.
 */
import { useId, type ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useForecastHeatmap, useForecastOverview } from "../api/hooks";
import type { ForecastDay } from "../api/types";
import { formatDate, formatDateRange } from "../utils/format";

function period(from: string, to: string): string {
  return formatDateRange(from, to, { year: false });
}

function forecastDates(t: TFunction, days: ForecastDay[]): string {
  const day = (iso: string) => formatDate(iso, { year: false, weekday: true });
  const first = days[0].service_date;
  const last = days[days.length - 1].service_date;
  return first === last ? day(first) : t("common.date_range", { from: day(first), to: day(last) });
}

/** The period line above the forecast: the dates it forecasts and what every
 *  other cell shows, or, with no forecast, that the grid is a typical week. */
export function ForecastPeriodNote({ aid, route }: { aid: number; route: string | null }) {
  const { t } = useTranslation();
  const overview = useForecastOverview(route == null ? aid : null);
  const heatmap = useForecastHeatmap(route != null ? aid : null, route ?? "");
  const data = route == null ? overview.data : heatmap.data;
  if (!data) return null;

  // The same emptiness the view below goes by, so no period is named above
  // its no-measurements message.
  const cells = "grid" in data ? data.grid : data.cells;
  const routes = "routes" in data ? data.routes : [];
  if (!cells.some((c) => c.expected_avg_min != null) && routes.length === 0) return null;
  const history = data.history_from && data.history_to ? period(data.history_from, data.history_to) : null;
  if (data.forecast_days.length > 0) {
    const pastShown = cells.some((c) => c.source !== "ml" && c.expected_avg_min != null);
    return (
      <p className="forecast-period" data-testid="fc-period">
        <strong>{t("forecast.period_forecast", { dates: forecastDates(t, data.forecast_days) })}</strong>
        {pastShown && (
          <span>
            {history ? t("forecast.period_rest_history", { period: history }) : t("forecast.period_rest_history_undated")}
          </span>
        )}
      </p>
    );
  }
  if (!history) return null;
  return (
    <p className="forecast-period" data-testid="fc-period">
      {t("forecast.period_typical", { period: history })}
    </p>
  );
}

/** The past-measurements part of the page, headed with the window it covers
 *  so it never reads as part of the forecast above it. `recentFrom`..`recentTo`
 *  is the window the routes' sparklines draw from. */
export function ForecastHistory({
  from,
  to,
  recentFrom,
  recentTo,
  children,
}: {
  from: string | null;
  to: string | null;
  recentFrom: string | null;
  recentTo: string | null;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const headingId = useId();
  return (
    <section className="forecast-history" aria-labelledby={headingId}>
      <h3 id={headingId}>
        {from && to ? t("forecast.history_heading", { period: period(from, to) }) : t("forecast.history_heading_undated")}
      </h3>
      <p>
        {t("forecast.history_routes_note")}
        {recentFrom && recentTo && <> {t("forecast.history_recent_note", { period: period(recentFrom, recentTo) })}</>}
      </p>
      {children}
    </section>
  );
}
