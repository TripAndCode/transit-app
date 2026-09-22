import { useSearchParams } from "react-router-dom";

/** One query-string write. `null` (or an empty array) removes the key. */
export type UrlPatch = Record<string, string | string[] | null>;

function applyPatch(prev: URLSearchParams, patch: UrlPatch): URLSearchParams {
  const next = new URLSearchParams(prev);
  for (const [key, value] of Object.entries(patch)) {
    if (value == null || value === "" || (Array.isArray(value) && value.length === 0)) {
      next.delete(key);
    } else {
      next.set(key, Array.isArray(value) ? value.join(",") : value);
    }
  }
  return next;
}

/**
 * Writes several query-string keys in a single navigation.
 *
 * `setSearchParams` builds its next value from the params of the render that
 * produced it, not from whatever the URL holds at call time. Two per-key
 * setters fired from one handler therefore both start from the same snapshot
 * and the second navigation drops the first one's key. Anything that changes
 * more than one key at once -- a selection made of a route plus a stop, or a
 * filter change that also clears what was selected under it -- has to go
 * through one call to survive.
 */
export function useUrlPatch(): (patch: UrlPatch) => void {
  const [, setParams] = useSearchParams();
  return function patch(next: UrlPatch) {
    setParams((prev) => applyPatch(prev, next), { replace: true });
  };
}

/**
 * Mirrors a single query-string key as React state, the same way
 * `useRangeContext` mirrors the whole date-range filter set: reading goes
 * through `useSearchParams` (so it survives navigation and is shareable via
 * copy-link), and writing uses `replace: true` (so per-tab selections don't
 * spam browser history the way a normal navigation would).
 *
 * The default value is never written to the URL — setting a key back to its
 * default removes the param instead of writing it explicitly, keeping the
 * URL free of redundant defaults (the same convention `ctxToQueryString`
 * follows for `dow`/`time_band`/`service`).
 *
 * Only for a key that moves on its own. Use `useUrlPatch` when one action
 * changes several keys; see its note on why per-key setters do not compose.
 */
export function useUrlState<T extends string>(
  key: string,
  defaultValue: T,
): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams();
  const value = (params.get(key) as T | null) ?? defaultValue;

  function set(next: T) {
    setParams(
      (prev) => {
        const nextParams = new URLSearchParams(prev);
        if (next === defaultValue) nextParams.delete(key);
        else nextParams.set(key, next);
        return nextParams;
      },
      { replace: true },
    );
  }

  return [value, set];
}
