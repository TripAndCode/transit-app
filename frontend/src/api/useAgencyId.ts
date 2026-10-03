import { useMatch } from "react-router-dom";

/**
 * The current agency id from the `/agencies/:agencyId/*` route segment.
 * Uses `useMatch` (rather than `useParams`) so it resolves correctly
 * regardless of where in the route tree it's called -- including above the
 * `:agencyId` route itself, in the shared `App` shell.
 * Returns `null` when there is no such segment or it isn't a plain integer.
 */
export function useAgencyId(): number | null {
  const raw = useMatch("/agencies/:agencyId/*")?.params.agencyId;
  if (!raw) return null;
  const id = Number(raw);
  return Number.isInteger(id) ? id : null;
}
