import { useState, type ReactNode } from "react";
import { TrendFocusCtx, type TrendFocus } from "./trendFocus";
import { dimRules } from "./trendFocusRules";

/**
 * Shares one hovered mark across the trend view's charts so a day, weekday or
 * hour highlighted in any of them narrows the others.
 *
 * The focus is React state here and nowhere else: it is written to this
 * wrapper's `data-focus-*` attributes and to the one `<style>` element below,
 * and the charts' marks respond through CSS attribute selectors
 * (`trendFocusRules.ts`). The context value is the setter alone, so a hover
 * re-renders this component and not a single chart. `display: contents` keeps
 * the wrapper out of layout.
 */
export function TrendFocusProvider({ children }: { children: ReactNode }) {
  const [focus, setFocus] = useState<TrendFocus | null>(null);
  return (
    <TrendFocusCtx.Provider value={setFocus}>
      <div
        className="trend-focus"
        style={{ display: "contents" }}
        data-focus-source={focus?.source}
        data-focus-date={focus?.date}
        data-focus-dow={focus?.dow}
      >
        <style>{dimRules(focus)}</style>
        {children}
      </div>
    </TrendFocusCtx.Provider>
  );
}
