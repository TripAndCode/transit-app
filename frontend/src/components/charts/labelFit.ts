import { useSyncExternalStore } from "react";

const ELLIPSIS = "…";

/** `text` whole when `measure` says it fits `budget`; otherwise its longest
 *  prefix that fits with an ellipsis after it, never less than one character.
 *  A prefix is cut between code points, so it never splits a character made
 *  of two UTF-16 units. */
export function fitLabel(text: string, budget: number, measure: (text: string) => number): string {
  if (measure(text) <= budget) return text;
  const chars = Array.from(text);
  let fits = 1;
  let over = chars.length;
  while (over - fits > 1) {
    const mid = Math.floor((fits + over) / 2);
    if (measure(chars.slice(0, mid).join("") + ELLIPSIS) <= budget) fits = mid;
    else over = mid;
  }
  return chars.slice(0, fits).join("") + ELLIPSIS;
}

type Measure = (text: string) => number;

/** One measurer per font size and font state, so a chart measured in a
 *  fallback font measures again once the web fonts have loaded. */
const measurers = new Map<string, Measure>();

function measurer(fontSize: number, fontsLoaded: boolean): Measure | null {
  if (typeof OffscreenCanvas === "undefined") return null;
  const key = `${fontSize}/${fontsLoaded}`;
  const cached = measurers.get(key);
  if (cached) return cached;
  const ctx = new OffscreenCanvas(1, 1).getContext("2d");
  if (!ctx) return null;
  ctx.font = `${fontSize}px ${getComputedStyle(document.body).fontFamily || "sans-serif"}`;
  const measure: Measure = (text) => ctx.measureText(text).width;
  measurers.set(key, measure);
  return measure;
}

/** Whether the page's web fonts have finished loading: read on first use,
 *  then flipped once by `document.fonts.ready` for every subscriber. A page
 *  without a FontFaceSet (jsdom) counts as loaded. */
let fontsLoaded: boolean | null = null;
const fontListeners = new Set<() => void>();

function fontsLoadedNow(): boolean {
  if (fontsLoaded === null) {
    const fonts = typeof document === "undefined" ? undefined : document.fonts;
    fontsLoaded = !fonts || fonts.status === "loaded";
    if (!fontsLoaded) {
      void fonts!.ready.then(() => {
        fontsLoaded = true;
        for (const listener of fontListeners) listener();
      });
    }
  }
  return fontsLoaded;
}

function subscribeFonts(listener: () => void): () => void {
  fontListeners.add(listener);
  return () => fontListeners.delete(listener);
}

/** A width function for SVG text at `fontSize` in the page's body font, in
 *  the units the size is given in, so a viewBox-unit size measures in
 *  viewBox units whatever the chart is scaled to. A new one comes once the
 *  web fonts load, re-rendering the caller. Null where nothing can measure
 *  text (no OffscreenCanvas, as in jsdom); the caller then draws labels
 *  whole. */
export function useSvgTextMeasurer(fontSize: number): Measure | null {
  const loaded = useSyncExternalStore(subscribeFonts, fontsLoadedNow, () => true);
  return measurer(fontSize, loaded);
}
