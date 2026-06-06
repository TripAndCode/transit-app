import {
  DEFAULT_RANGE_DAYS,
  isoDaysAgo,
  todayISO,
  type RangeCtx,
} from "../../api/rangeContext";
import type { FilterCtx } from "../../api/types";

/** Convert URL-based RangeCtx to FilterCtx for new thread seeding. */
export function rangeCtxToFilterCtx(ctx: RangeCtx): FilterCtx {
  return {
    from_date: ctx.from,
    to_date: ctx.to,
    dow: ctx.dow !== "all" ? ctx.dow : undefined,
    time_band: ctx.time_band !== "all" ? ctx.time_band : undefined,
    service: ctx.service !== "all" ? ctx.service : undefined,
    routes: ctx.routes.length > 0 ? ctx.routes : undefined,
  };
}

/** Derive a FilterCtx from a conversation's stored filter_ctx, with defaults. */
export function resolvedFilterCtx(fc: FilterCtx | undefined | null): FilterCtx {
  const today = todayISO();
  const fromDefault = isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
  return {
    from_date: fc?.from_date ?? fromDefault,
    to_date: fc?.to_date ?? today,
    dow: fc?.dow ?? "all",
    time_band: fc?.time_band ?? "all",
    service: fc?.service ?? "all",
    routes: fc?.routes ?? [],
  };
}
