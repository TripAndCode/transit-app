import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";

/** One day of the agency's departures, every route or one: the delay
 *  record's lookup scope, and the staff list's for the same day. */
export function certificateDayScope(date: string, route: string | null): Scope {
  return {
    ...SCOPE_EXTRAS_NONE,
    from: date,
    to: date,
    dow: "all",
    time_band: "all",
    service: "all",
    routes: route != null ? [route] : [],
  };
}
