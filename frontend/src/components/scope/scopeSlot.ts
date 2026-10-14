import { createContext, use } from "react";

type ScopeSlot = { slot: HTMLElement | null; setSlot: (el: HTMLElement | null) => void };

/** The place on a page heading's row that the scope sentence further down
 *  puts its actions in. The heading and the sentence are often rendered by
 *  different components, a lazy one among them, so the place travels by
 *  context rather than props. */
export const ScopeSlotContext = createContext<ScopeSlot | null>(null);

/** Where the scope sentence's actions go: the heading's place, or null to
 *  keep them beside the sentence. */
export function useScopeActionsSlot(): HTMLElement | null {
  return use(ScopeSlotContext)?.slot ?? null;
}
