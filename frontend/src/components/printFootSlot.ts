import { createContext, use } from "react";

type PrintFootSlot = { slot: HTMLElement | null; setSlot: (el: HTMLElement | null) => void };

/** The place in the printed page's foot (PrintFrame) where a screen adds a
 *  line of its own for the figures it shows. The frame and the screen are
 *  far apart in the tree, so the place travels by context. */
export const PrintFootSlotContext = createContext<PrintFootSlot | null>(null);

/** Where a screen's line goes in the printed foot, or null where no print
 *  frame is drawn (a page outside the app shell). */
export function usePrintFootSlot(): HTMLElement | null {
  return use(PrintFootSlotContext)?.slot ?? null;
}
