import type { TFunction } from "i18next";
import type { ForecastDay } from "../api/types";
import { formatShortDate } from "./format";

/** A weekday row's label, with the date its forecast is for when the forecast
 *  model covers that weekday. */
export function datedDayLabel(t: TFunction, dayLabel: (dow: number) => string, days: ForecastDay[]): (dow: number) => string {
  const dates = new Map(days.map((d) => [d.dow, d.service_date]));
  return (dow) => {
    const iso = dates.get(dow);
    return iso ? t("forecast.dow_dated", { day: dayLabel(dow), date: formatShortDate(iso) }) : dayLabel(dow);
  };
}
