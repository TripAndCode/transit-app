import { useEffect, useRef, useState } from "react";
import { apiPost } from "./client";
import type { RangeCtx } from "./rangeContext";

export type CopilotInsight = { text: string; cite: string; lowConfidence: boolean };

const DEBOUNCE_MS = 800;

export function useCopilotInsight(
  agencyId: number | null,
  tab: string | null,
  filters: RangeCtx,
  viewPayload: unknown,
): { insight: CopilotInsight | null; loading: boolean; error: unknown } {
  // `insight` is tagged with the request key it answers, and the value handed
  // back to the caller is derived by comparing that tag to the current key —
  // this is what lets a stale answer get hidden the instant the tab/filters
  // change, without a synchronous `setState` at the top of the effect just to
  // null it out (react-hooks/set-state-in-effect forbids that; CLAUDE.md
  // prefers derived state over a synchronization effect here anyway).
  const [state, setState] = useState<{ key: string | null; insight: CopilotInsight | null }>({
    key: null,
    insight: null,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const key =
    agencyId == null || tab == null || !viewPayload
      ? null
      : `${agencyId}:${tab}:${JSON.stringify(filters)}:${JSON.stringify(viewPayload)}`;

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (key == null) return;
    timerRef.current = setTimeout(() => {
      setLoading(true);
      setError(null);
      apiPost<{ text: string; cite: string; low_confidence: boolean }>(`/api/${agencyId}/copilot/insight`, {
        tab,
        filters,
        view_payload: viewPayload,
      })
        .then((res) => setState({ key, insight: { text: res.text, cite: res.cite, lowConfidence: res.low_confidence } }))
        .catch((err) => setError(err))
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const insight = state.key === key ? state.insight : null;
  return { insight, loading, error };
}
