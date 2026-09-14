import { useSearchParams } from "react-router-dom";

export function useInvestigationLocation(): [string | null, (id: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("conversation");
  // IDs become an API path segment. Accept UUIDs and the anonymous fallback,
  // never path separators or query-string syntax from an arbitrary deep link.
  const id = raw && /^[a-zA-Z0-9][a-zA-Z0-9.-]{0,99}$/.test(raw) ? raw : null;
  return [id, (nextId) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (nextId) next.set("conversation", nextId);
      else next.delete("conversation");
      return next;
    }, { replace: true });
  }];
}
