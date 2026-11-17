import { applyScopePatch, defaultPeriod, parseScope, type Scope, type ScopePatch } from "../../api/scope";
import type { ScopeApplied } from "../../components/scope/scopePhrases";
import type { FilterCtx } from "../../api/types";

/** Convert URL-based Scope to FilterCtx for new thread seeding. */
export function rangeCtxToFilterCtx(ctx: Scope): FilterCtx {
  return {
    from_date: ctx.from,
    to_date: ctx.to,
    dow: ctx.dow !== "all" ? ctx.dow : undefined,
    time_band: ctx.time_band !== "all" ? ctx.time_band : undefined,
    service: ctx.service !== "all" ? ctx.service : undefined,
    routes: ctx.routes.length > 0 ? ctx.routes : undefined,
  };
}

/** Derive a FilterCtx from a conversation's stored filter_ctx, with defaults.
 *  `dataEnd` is the agency's latest data day (DataEndContext). */
export function resolvedFilterCtx(fc: FilterCtx | undefined | null, dataEnd: string | null): FilterCtx {
  const period = defaultPeriod(dataEnd);
  return {
    from_date: fc?.from_date ?? period.from,
    to_date: fc?.to_date ?? period.to,
    dow: fc?.dow ?? "all",
    time_band: fc?.time_band ?? "all",
    service: fc?.service ?? "all",
    routes: fc?.routes ?? [],
  };
}

/** A thread's conditions in the URL's spelling, so the scope's own parser
 *  validates them as it does a link's. */
function filterCtxParams(fc: FilterCtx): URLSearchParams {
  const params = new URLSearchParams();
  if (fc.from_date) params.set("from", fc.from_date);
  if (fc.to_date) params.set("to", fc.to_date);
  if (fc.dow) params.set("dow", fc.dow);
  if (fc.time_band) params.set("time_band", fc.time_band);
  if (fc.service) params.set("service", fc.service);
  if (fc.routes?.length) params.set("routes", fc.routes.join(","));
  return params;
}

/** A thread's conditions as the scope the scope bar reads. */
export function threadScope(fc: FilterCtx, dataEnd: string | null): Scope {
  return parseScope(filterCtxParams(fc), defaultPeriod(dataEnd));
}

/** A thread's conditions after a scope bar change. */
export function patchThreadFilter(fc: FilterCtx, patch: ScopePatch, dataEnd: string | null): FilterCtx {
  return rangeCtxToFilterCtx(parseScope(applyScopePatch(filterCtxParams(fc), patch), defaultPeriod(dataEnd)));
}

/** A thread stores the period, days, time band, timetable and routes, and
 *  no other condition (api/routers/conversations.py, _ctx_from_stored_filters). */
export const THREAD_SCOPE_APPLIED: ScopeApplied = {
  from: true,
  to: true,
  dow: true,
  time_band: true,
  service: true,
  routes: true,
  hour: false,
  stop: false,
  dir: false,
  late: false,
  early: false,
};

/** The conditions a thread does not store. */
const NOT_STORED = ["hour", "stop", "dir", "late", "early"] as const;

/** The scope the notebook's scope bar shows. With no thread open it keeps
 *  the screen's URL values for the conditions a thread would not store,
 *  which the bar leaves out (`THREAD_SCOPE_APPLIED`); an open thread has
 *  none of them. */
export function notebookScope(fc: FilterCtx, urlScope: Scope | null, dataEnd: string | null): Scope {
  const scope = threadScope(fc, dataEnd);
  if (!urlScope) return scope;
  const kept = Object.fromEntries(NOT_STORED.map((key) => [key, urlScope[key]]));
  return { ...scope, ...kept };
}

/** A scope bar change split into what a thread stores and the rest, which
 *  only the URL scope can hold. */
export function splitNotebookPatch(patch: ScopePatch): { thread: ScopePatch; url: ScopePatch } {
  const thread: ScopePatch = { ...patch };
  const url: ScopePatch = {};
  for (const key of NOT_STORED) {
    if (key in patch) {
      Object.assign(url, { [key]: patch[key] });
      delete thread[key];
    }
  }
  return { thread, url };
}
