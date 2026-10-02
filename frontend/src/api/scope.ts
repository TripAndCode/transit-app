import { createContext, useContext } from "react";
import { useSearchParams } from "react-router-dom";

export type Weekday = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
/** A legacy group, or a Monday-first comma list of weekdays (see canonicalDow). */
export type DowFilter = "all" | "weekday" | "weekend" | Weekday | `${Weekday},${string}`;
export type ServiceFilter = "all" | "平日" | "土日祝"; // i18n-ignore: query contract
export type TimeBand = "all" | "morning" | "forenoon" | "noon" | "afternoon" | "evening" | "night" | "late_night";

/** The shared scope every screen, link, share URL and saved analysis carries.
 *  Screen-local params (`report`, `sort`, `by`, `doc`, `tab`, `sub_tab`, `at`,
 *  and the route page's `compare=1` week-earlier overlay) are not part of it. */
export type Scope = {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
  dow: DowFilter;
  time_band: TimeBand;
  service: ServiceFilter;
  routes: string[];
  /** Inclusive hour range; excludes a time_band other than "all". */
  hour: [number, number] | null;
  stop: string | null;
  dir: 0 | 1 | null;
  /** On-time tolerances in seconds. */
  late: number | null;
  early: number | null;
};

/** `null` clears a param (and, for from/to, lets useDefaultRangeAnchor
 *  re-derive the default). */
export type ScopePatch = { [K in keyof Scope]?: Scope[K] | null };

export const SCOPE_EXTRAS_NONE = { hour: null, stop: null, dir: null, late: null, early: null } as const;

const SCOPE_PARAMS = ["from", "to", "dow", "time_band", "service", "routes", "hour", "stop", "dir", "late", "early"] as const;
type ScopeParam = (typeof SCOPE_PARAMS)[number];

export const WEEKDAYS: readonly Weekday[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const TIME_BANDS: readonly string[] = ["all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"];
const SERVICES: readonly string[] = ["all", "平日", "土日祝"]; // i18n-ignore: query contract
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_ROUTES = 100;
const MAX_STOP_ID_LEN = 128;
const MAX_TOLERANCE_SEC = 3600;

function isDate(raw: string): boolean {
  return ISO_DATE.test(raw) && !Number.isNaN(Date.parse(`${raw}T00:00:00Z`)) && new Date(`${raw}T00:00:00Z`).toISOString().startsWith(raw);
}

/** Same rule as api/range.py canonical_dow: dedupe, Monday-first order, and
 *  fold a list that names exactly a legacy group into that group. */
function canonicalDow(raw: string): DowFilter | null {
  if (raw === "all" || raw === "weekday" || raw === "weekend") return raw;
  const names = raw.split(",");
  if (names.some((n) => !(WEEKDAYS as readonly string[]).includes(n))) return null;
  const key = WEEKDAYS.filter((d) => names.includes(d)).join(",");
  if (key === WEEKDAYS.join(",")) return "all";
  if (key === "mon,tue,wed,thu,fri") return "weekday";
  if (key === "sat,sun") return "weekend";
  return key as DowFilter;
}

function parseHour(raw: string): [number, number] | null {
  const m = /^(\d{1,2})(?:-(\d{1,2}))?$/.exec(raw);
  if (!m) return null;
  const start = Number(m[1]);
  const end = m[2] === undefined ? start : Number(m[2]);
  return start <= 23 && end <= 23 && start <= end ? [start, end] : null;
}

/** One validator per param: the canonical text to write, or null when the
 *  value is invalid or the default (so it is omitted). */
function canon(key: ScopeParam, raw: string): string | null {
  switch (key) {
    case "from":
    case "to":
      return isDate(raw) ? raw : null;
    case "dow": {
      const dow = canonicalDow(raw);
      return dow === null || dow === "all" ? null : dow;
    }
    case "time_band":
      return TIME_BANDS.includes(raw) && raw !== "all" ? raw : null;
    case "service":
      return SERVICES.includes(raw) && raw !== "all" ? raw : null;
    case "routes": {
      const routes = [...new Set(raw.split(",").map((r) => r.trim()).filter(Boolean))].slice(0, MAX_ROUTES);
      return routes.length > 0 ? routes.join(",") : null;
    }
    case "hour": {
      const hour = parseHour(raw);
      return hour === null ? null : hour[0] === hour[1] ? String(hour[0]) : `${hour[0]}-${hour[1]}`;
    }
    case "stop": {
      const stop = raw.trim();
      return stop && stop.length <= MAX_STOP_ID_LEN ? stop : null;
    }
    case "dir":
      return raw === "0" || raw === "1" ? raw : null;
    case "late":
    case "early":
      return /^\d{1,4}$/.test(raw) && Number(raw) <= MAX_TOLERANCE_SEC ? String(Number(raw)) : null;
  }
}

function rawOf(key: ScopeParam, value: unknown): string | null {
  if (value == null) return null;
  if (key === "routes" && Array.isArray(value)) return value.join(",");
  if (key === "hour" && Array.isArray(value)) return `${value[0]}-${value[1]}`;
  return String(value);
}

export function parseScope(params: URLSearchParams, defaults: { from: string; to: string }): Scope {
  const get = (key: ScopeParam) => {
    const raw = params.get(key);
    return raw == null ? null : canon(key, raw);
  };
  const hourText = get("hour");
  const hour = hourText === null ? null : parseHour(hourText);
  const num = (key: ScopeParam) => {
    const text = get(key);
    return text === null ? null : Number(text);
  };
  return {
    from: get("from") ?? defaults.from,
    to: get("to") ?? defaults.to,
    dow: (get("dow") ?? "all") as DowFilter,
    time_band: hour ? "all" : ((get("time_band") ?? "all") as TimeBand),
    service: (get("service") ?? "all") as ServiceFilter,
    routes: get("routes")?.split(",") ?? [],
    hour,
    stop: get("stop"),
    dir: num("dir") as 0 | 1 | null,
    late: num("late"),
    early: num("early"),
  };
}

/** Canonical query for whatever `rawFor` yields per param. A valid `hour`
 *  takes the place of `time_band`, so only one of them is ever written. */
function canonicalQuery(rawFor: (key: ScopeParam) => string | null): string {
  const hourRaw = rawFor("hour");
  const hasHour = hourRaw !== null && canon("hour", hourRaw) !== null;
  const out = new URLSearchParams();
  for (const key of SCOPE_PARAMS) {
    if (key === "time_band" && hasHour) continue;
    const raw = rawFor(key);
    const text = raw === null ? null : canon(key, raw);
    if (text !== null) out.set(key, text);
  }
  return out.toString();
}

export function scopeToQueryString(scope: Scope): string {
  return canonicalQuery((key) => rawOf(key, scope[key]));
}

/** The scope a URL states outright, canonical, without the default period
 *  filled in. A remembered scope then keeps following the rolling default
 *  window instead of freezing the dates it was recorded on. */
export function explicitScopeQuery(search: string): string {
  const params = new URLSearchParams(search);
  return canonicalQuery((key) => params.get(key));
}

export function applyScopePatch(prev: URLSearchParams, patch: ScopePatch): URLSearchParams {
  const next = new URLSearchParams(prev);
  for (const key of SCOPE_PARAMS) {
    if (!(key in patch)) continue;
    const raw = rawOf(key, patch[key]);
    const text = raw === null ? null : canon(key, raw);
    if (text === null) next.delete(key);
    else next.set(key, text);
  }
  if ("hour" in patch && next.has("hour")) next.delete("time_band");
  else if ("time_band" in patch && next.has("time_band")) next.delete("hour");
  return next;
}

/** A stored preset replaces the whole scope: fields it predates are cleared,
 *  and applyScopePatch validates whatever the stored JSON holds. */
export function presetScopePatch(rangeCtx: Record<string, unknown>): ScopePatch {
  return { ...SCOPE_EXTRAS_NONE, ...rangeCtx } as ScopePatch;
}

export const DEFAULT_RANGE_DAYS = 30;

/** JST is the only timezone the server uses (see api/main.py _init_connection). */
const JST_TZ = "Asia/Tokyo";

// en-CA renders YYYY-MM-DD without locale-specific separators.
const jstFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: JST_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Format a Date as YYYY-MM-DD in JST. */
export function toJstISO(d: Date): string {
  return jstFmt.format(d);
}

/** YYYY-MM-DD today in JST. */
export function todayISO(): string {
  return toJstISO(new Date());
}

/** YYYY-MM-DD `days` calendar days before today, in JST. */
export function isoDaysAgo(days: number): string {
  return toJstISO(new Date(Date.now() - days * 86_400_000));
}

/** YYYY-MM-DD `days` calendar days before the given YYYY-MM-DD date — pure
 *  date-string arithmetic, no "now" involved (unlike isoDaysAgo). Used to
 *  anchor a default range at an agency's real latest data date instead of
 *  today, when today's default window would otherwise be empty. */
export function isoDaysBefore(dateISO: string, days: number): string {
  const d = new Date(`${dateISO}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return toJstISO(d);
}

/** YYYY-MM-DD of the last closed JST day (yesterday). History and every
 *  aggregate hold closed days only, so this, not today, ends the default
 *  report period; `api.range.last_closed_jst_day` is the server's twin. */
export function lastClosedDayISO(): string {
  return isoDaysAgo(1);
}

/** The default report period: DEFAULT_RANGE_DAYS closed days ending on
 *  lastClosedDayISO(). Every default-range reader goes through this one window. */
export function defaultPeriod(): { from: string; to: string } {
  const to = lastClosedDayISO();
  return { from: isoDaysBefore(to, DEFAULT_RANGE_DAYS - 1), to };
}

/** JST calendar (year, month=1..12) of a Date. */
export function jstYearMonth(d: Date): { year: number; month: number } {
  const parts = jstFmt.formatToParts(d);
  return {
    year: Number(parts.find((p) => p.type === "year")!.value),
    month: Number(parts.find((p) => p.type === "month")!.value),
  };
}

/** The route a route page is about. Inside it, a URL with no `routes` param
 *  still scopes to that route, so opening a route's page never writes a
 *  route filter the rest of the app would then carry. */
export const ScopeRouteContext = createContext<string | null>(null);

export function useScope(): [Scope, (patch: ScopePatch) => void] {
  const [params, setParams] = useSearchParams();
  const pageRoute = useContext(ScopeRouteContext);
  const parsed = parseScope(params, defaultPeriod());
  const scope = pageRoute != null && parsed.routes.length === 0 ? { ...parsed, routes: [pageRoute] } : parsed;
  function update(patch: ScopePatch) {
    setParams((prev) => applyScopePatch(prev, patch), { replace: true });
  }
  return [scope, update];
}
