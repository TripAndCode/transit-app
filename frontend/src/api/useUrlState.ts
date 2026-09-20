import { useSearchParams } from "react-router-dom";

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
