import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiPost } from "./client";
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

export function useCopilotInsight(
  agencyId: number | null,
  tab: string | null,
  filters: RangeCtx,
  viewPayload: unknown,
): { insight: CopilotInsight | null; loading: boolean; error: unknown } {
  const key = buildKey(agencyId, tab, filters, viewPayload);

  // Only the request *key* (and the params it was built from) are debounced
  // here — the actual fetch, loading/error state, and stale-response discard
  // are owned by useQuery (queryKey ["copilot-insight", debouncedKey]), same
  // split as AdminUsersPage's search box (debounce the value, let useQuery
  // own the request lifecycle). That's what makes a superseded in-flight
  // request's response get thrown away automatically instead of racing a
  // newer one into `state`, and what makes `loading`/`error` reset to
  // false/null the instant the query is disabled (key null) rather than
  // sticking around across tab navigation.
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
    queryFn: () => {
      const params = debounced.params!;
      return apiPost<{ text: string; cite: string; low_confidence: boolean }>(
        `/api/${params.agencyId}/copilot/insight`,
        { tab: params.tab, filters: params.filters, view_payload: params.viewPayload },
      );
    },
    enabled: debounced.params != null,
  });

  const insight = data ? { text: data.text, cite: data.cite, lowConfidence: data.low_confidence } : null;
  return { insight, loading: isLoading, error };
}
