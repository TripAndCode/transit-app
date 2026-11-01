import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";

/** One day of the agency's departures on every route: the scope of the staff
 *  list of late departures beside the delay record's lookup. */
export function certificateDayScope(date: string): Scope {
  return {
    ...SCOPE_EXTRAS_NONE,
    from: date,
    to: date,
    dow: "all",
    time_band: "all",
    service: "all",
    routes: [],
  };
}
