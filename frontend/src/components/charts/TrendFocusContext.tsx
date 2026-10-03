import { useState, type ReactNode } from "react";
import { TrendFocusCtx, type TrendFocus } from "./trendFocus";

/**
 * Shares one hovered mark across the trend view's charts so a day, weekday or
 * hour highlighted in any of them narrows the others.
 *
 * The focus is React state, not a ref: a ref written during render is a
 * React Compiler error, and the dimming has to re-render the linked charts
 * anyway. The context value and the `useTrendFocus` hook live in
 * `trendFocus.ts` so this file exports a component and nothing else.
 */
export function TrendFocusProvider({ children }: { children: ReactNode }) {
  const [focus, setFocus] = useState<TrendFocus | null>(null);
  return <TrendFocusCtx.Provider value={{ focus, setFocus }}>{children}</TrendFocusCtx.Provider>;
}
