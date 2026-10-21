import type { OverviewDayType } from "../api/types";

/** The i18n key naming each day type (平日 / 土曜 / 日祝 / 土日祝). */
export const DAY_TYPE_KEY: Record<OverviewDayType, string> = {
  weekday: "overview.service_split.type.weekday",
  saturday: "overview.service_split.type.saturday",
  sunday_holiday: "overview.service_split.type.sunday_holiday",
  weekend_holiday: "overview.service_split.type.weekend_holiday",
};
