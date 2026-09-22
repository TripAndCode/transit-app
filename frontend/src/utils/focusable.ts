/** What counts as tabbable inside an overlay that traps focus. One
 *  definition rather than one per trap: two copies drift the moment either
 *  one learns about a new focusable kind, and nothing reports the gap --
 *  the overlay with the stale copy just lets Tab escape it. */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}
