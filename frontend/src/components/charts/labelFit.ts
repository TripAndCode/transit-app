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

/** A width function for SVG text at `fontSize` in the page's body font, in
 *  the units the size is given in, so a viewBox-unit size measures in
 *  viewBox units whatever the chart is scaled to. Null where nothing can
 *  measure text (no OffscreenCanvas, as in jsdom); the caller then draws
 *  labels whole. */
export function svgTextMeasurer(fontSize: number): ((text: string) => number) | null {
  if (typeof OffscreenCanvas === "undefined") return null;
  const ctx = new OffscreenCanvas(1, 1).getContext("2d");
  if (!ctx) return null;
  const family = getComputedStyle(document.body).fontFamily || "sans-serif";
  ctx.font = `${fontSize}px ${family}`;
  return (text) => ctx.measureText(text).width;
}
