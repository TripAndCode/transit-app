import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type DowFilter = "all" | "weekday" | "weekend";
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
};

export const DEFAULT_RANGE_DAYS = 30;

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

export function useRangeContext(): [RangeCtx, (patch: Partial<RangeCtx>) => void] {
  const [params, setParams] = useSearchParams();

  const ctx = useMemo<RangeCtx>(() => {
    const to = params.get("to") || todayISO();
    const from = params.get("from") || isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
    const dow = (params.get("dow") as DowFilter) || "all";
    const time_band = (params.get("time_band") as TimeBand) || "all";
    return { from, to, dow, time_band };
  }, [params]);

  const update = useCallback(
    (patch: Partial<RangeCtx>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v == null) next.delete(k);
            else next.set(k === "from" ? "from" : k === "to" ? "to" : k, String(v));
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
  return u.toString();
}
