import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type { RangeCtx } from "./rangeContext";

export type CopilotInsight = { text: string; cite: string; lowConfidence: boolean };

const DEBOUNCE_MS = 800;

type CopilotParams = {
  agencyId: number;
  tab: string;
  filters: RangeCtx;
  viewPayload: unknown;
};

function buildKey(
  agencyId: number | null,
  tab: string | null,
  filters: RangeCtx,
  viewPayload: unknown,
): string | null {
  return agencyId == null || tab == null || !viewPayload
    ? null
    : `${agencyId}:${tab}:${JSON.stringify(filters)}:${JSON.stringify(viewPayload)}`;
}

/** The Copilot kill switch (`COPILOT_INSIGHT_ENABLED` server-side).
 *
 * Cached for an hour like `useFollowupEnabled`: it is deployment
 * configuration, not per-request state. Callers must treat anything other
 * than an explicit `true` as off, so an unresolved or failed check never
 * fires the billed insight POST.
 */
export function useCopilotEnabled(agencyId: number | null) {
  return useQuery({
    queryKey: ["copilot-enabled", agencyId],
    queryFn: ({ signal }) =>
      apiGet<{ enabled: boolean }>(`/api/${agencyId}/copilot/enabled`, { signal }),
    enabled: agencyId != null,
    staleTime: 60 * 60 * 1000,
  });
}

export function useCopilotInsight(
  agencyId: number | null,
  tab: string | null,
  filters: RangeCtx,
  viewPayload: unknown,
): { insight: CopilotInsight | null; loading: boolean; error: unknown } {
  const key = buildKey(agencyId, tab, filters, viewPayload);

  // Only the request *key* is debounced here; useQuery (queryKey
  // ["copilot-insight", debouncedKey]) owns the fetch, loading/error state,
  // and stale-response discard — same split as AdminUsersPage's search box.
  // `[key]` alone as the effect dep is enough: `params` is derived from the
  // same inputs that produce `key`, so a `key` change always means fresh
  // `params` too.
  const [debounced, setDebounced] = useState<{ key: string | null; params: CopilotParams | null }>({
    key,
    params: key == null ? null : { agencyId: agencyId!, tab: tab!, filters, viewPayload },
  });

  useEffect(() => {
    if (key === debounced.key) return;
    const id = setTimeout(() => {
      setDebounced({
        key,
        params: key == null ? null : { agencyId: agencyId!, tab: tab!, filters, viewPayload },
      });
    }, DEBOUNCE_MS);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["copilot-insight", debounced.key],
    queryFn: ({ signal }) => {
      const params = debounced.params!;
      return apiPost<{ text: string; cite: string; low_confidence: boolean }>(
        `/api/${params.agencyId}/copilot/insight`,
        { tab: params.tab, filters: params.filters, view_payload: params.viewPayload },
        { signal },
      );
    },
    enabled: debounced.params != null,
    // This POST consumes one anonymous-quota unit per attempt with no
    // server-side refund on failure, so react-query's default retry would
    // silently burn a second unit for what the user experiences as one
    // request. Never retry it, regardless of the global QueryClient default.
    retry: false,
    // One insight per view state, not per subscription. Without this, leaving
    // Overview and coming back re-runs the query for an unchanged key and
    // bills another LLM call — the debounce above only coalesces key changes,
    // it does not stop a refetch for a key that is already cached.
    staleTime: 10 * 60 * 1000,
  });

  const insight = data ? { text: data.text, cite: data.cite, lowConfidence: data.low_confidence } : null;
  return { insight, loading: isLoading, error };
}
