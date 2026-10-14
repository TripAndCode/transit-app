import type { ServiceFilter } from "./scope";
import type { OverviewDayType } from "./types";

/** A service filter value that names a timetable, i.e. anything but "all". */
type TimetableService = Exclude<ServiceFilter, "all">;

/** The service filter value naming each day-type bucket; api/range.py's
 *  DAY_TYPE_SERVICE is the backend side of the same table. */
const SERVICE_OF_DAY_TYPE: Record<OverviewDayType, TimetableService> = {
  weekday: "平日", // i18n-ignore: query contract
  saturday: "土曜", // i18n-ignore: query contract
  sunday_holiday: "日祝", // i18n-ignore: query contract
  weekend_holiday: "土日祝", // i18n-ignore: query contract
};

/** The split the backend's weekday rule gives a feed whose calendar is not
 *  known yet. */
const DEFAULT_DAY_TYPES: readonly OverviewDayType[] = ["weekday", "saturday", "sunday_holiday"];

/** The timetable choices for an agency whose calendar splits the period into
 *  `dayTypes`, and the choice `service` shows as among them. The URL value is
 *  never rewritten: on a feed split two ways, 土曜 and 日祝 each select its
 *  土日祝 (api/range.py's service_day_types) and show as that; 土日祝 on a feed
 *  split three ways selects both weekend buckets, so it keeps a choice of its
 *  own. */ // i18n-ignore: JSDoc examples
export function timetableChoices(
  service: ServiceFilter,
  dayTypes: readonly OverviewDayType[] = DEFAULT_DAY_TYPES,
): { options: TimetableService[]; shown: ServiceFilter } {
  const options = dayTypes.map((dayType) => SERVICE_OF_DAY_TYPE[dayType]);
  if (service === "all" || options.includes(service)) return { options, shown: service };
  if ((service === "土曜" || service === "日祝") && options.includes("土日祝")) return { options, shown: "土日祝" }; // i18n-ignore: query contract
  return { options: [...options, service], shown: service };
}
