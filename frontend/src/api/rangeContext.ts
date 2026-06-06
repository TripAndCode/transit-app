import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type DowFilter = "all" | "weekday" | "weekend";
export type ServiceFilter = "all" | "平日" | "土日祝"; // i18n-ignore: query contract
export type TimeBand =
  | "all"
  | "morning"
  | "forenoon"
  | "noon"
  | "afternoon"
  | "evening"
  | "night"
  | "late_night";

export type RangeCtx = {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
  dow: DowFilter;
  time_band: TimeBand;
  service: ServiceFilter;
  routes: string[];
};

type RangeCtxPatch = {
  from?: string;
  to?: string;
  dow?: DowFilter;
  time_band?: TimeBand;
  service?: ServiceFilter;
  /** Pass `null` to clear, otherwise the new array. */
  routes?: string[] | null;
};

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

/** YYYY-MM-DD today in JST. Default `to` for the date-range UI. */
export function todayISO(): string {
  return toJstISO(new Date());
}

/** YYYY-MM-DD `days` calendar days before today, in JST. */
export function isoDaysAgo(days: number): string {
  return toJstISO(new Date(Date.now() - days * 86_400_000));
}

/** JST calendar (year, month=1..12) of a Date. */
export function jstYearMonth(d: Date): { year: number; month: number } {
  const parts = jstFmt.formatToParts(d);
  return {
    year: Number(parts.find((p) => p.type === "year")!.value),
    month: Number(parts.find((p) => p.type === "month")!.value),
  };
}

export function useRangeContext(): [RangeCtx, (patch: RangeCtxPatch) => void] {
  const [params, setParams] = useSearchParams();

  const ctx = useMemo<RangeCtx>(() => {
    const to = params.get("to") || todayISO();
    const from = params.get("from") || isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
    const dow = (params.get("dow") as DowFilter) || "all";
    const time_band = (params.get("time_band") as TimeBand) || "all";
    const service = (params.get("service") as ServiceFilter) || "all";
    const routesStr = params.get("routes");
    const routes = routesStr ? routesStr.split(",").filter(Boolean) : [];
    return { from, to, dow, time_band, service, routes };
  }, [params]);

  const update = useCallback(
    (patch: RangeCtxPatch) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (k === "routes") {
              if (v == null || (Array.isArray(v) && v.length === 0)) next.delete("routes");
              else next.set("routes", (v as string[]).join(","));
              continue;
            }
            if (v == null) next.delete(k);
            else next.set(k, String(v));
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return [ctx, update];
}

export function ctxToQueryString(ctx: RangeCtx): string {
  const u = new URLSearchParams();
  u.set("from", ctx.from);
  u.set("to", ctx.to);
  if (ctx.dow !== "all") u.set("dow", ctx.dow);
  if (ctx.time_band !== "all") u.set("time_band", ctx.time_band);
  if (ctx.service !== "all") u.set("service", ctx.service);
  if (ctx.routes.length > 0) u.set("routes", ctx.routes.join(","));
  return u.toString();
}
