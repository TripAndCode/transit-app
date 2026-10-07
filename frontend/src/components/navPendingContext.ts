import { createContext, use } from "react";

type NavPending = { pendingTo: string | null; go: (to: string) => void };

export const NavPendingContext = createContext<NavPending | null>(null);

/** Where the navigation under way is going, or null when none is. */
export function usePendingNavTarget(): string | null {
  return use(NavPendingContext)?.pendingTo ?? null;
}
