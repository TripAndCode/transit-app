import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

/**
 * Roving focus over a grid of read-only value cells: the widget is one tab
 * stop and the arrow keys move inside it.
 *
 * A tab stop per cell is the obvious way to make a heatmap keyboard-reachable
 * and the wrong one -- a day x hour grid then sits 168 Tab presses deep in
 * front of everything after it on the page, which is its own barrier. This is
 * the composite-widget pattern ARIA has for that.
 *
 * `slots` is row-major and may hold `null` where a position renders nothing
 * focusable; navigation skips those rather than landing on them. Each
 * non-null slot names its cell and is unique, so the tab stop follows the
 * cell, not its position, when a redraw moves it. The container is a div by
 * default, or an SVG group for a chart drawn in SVG.
 */
export function useRovingCells<Container extends Element = HTMLDivElement>(slots: (string | null)[], columns: number) {
  const firstFilled = slots.findIndex((slot) => slot !== null);
  const [requested, setRequested] = useState<string | null>(slots[firstFilled] ?? null);
  const containerRef = useRef<Container>(null);
  // Derived, not synchronised: the data can change under a held cell, and an
  // effect correcting it afterwards would render one frame with no tab stop.
  const held = requested == null ? -1 : slots.indexOf(requested);
  const active = held >= 0 ? held : firstFilled;

  function step(from: number, delta: number): number | null {
    if (Math.abs(delta) === 1) {
      const row = Math.floor(from / columns);
      for (let i = from + delta; i >= 0 && i < slots.length && Math.floor(i / columns) === row; i += delta) {
        if (slots[i] !== null) return i;
      }
      return null;
    }
    const target = from + delta;
    if (target < 0 || target >= slots.length || slots[target] === null) return null;
    return target;
  }

  function edgeOfRow(from: number, side: "first" | "last"): number | null {
    const row = Math.floor(from / columns);
    const indices = [];
    for (let i = row * columns; i < Math.min((row + 1) * columns, slots.length); i++) {
      if (slots[i] !== null) indices.push(i);
    }
    return (side === "first" ? indices[0] : indices.at(-1)) ?? null;
  }

  function onKeyDown(event: ReactKeyboardEvent) {
    const next =
      event.key === "ArrowRight"
        ? step(active, 1)
        : event.key === "ArrowLeft"
          ? step(active, -1)
          : event.key === "ArrowDown"
            ? step(active, columns)
            : event.key === "ArrowUp"
              ? step(active, -columns)
              : event.key === "Home"
                ? edgeOfRow(active, "first")
                : event.key === "End"
                  ? edgeOfRow(active, "last")
                  : null;
    if (next === null) return;
    event.preventDefault();
    setRequested(slots[next]);
    // Focused straight from the handler rather than from an effect on
    // `active`: an effect would also fire on first render and pull focus into
    // the grid before anyone asked for it.
    containerRef.current?.querySelector<HTMLElement | SVGElement>(`[data-cell="${next}"]`)?.focus();
  }

  function onCellFocus(index: number) {
    setRequested(slots[index]);
  }

  return { containerRef, activeSlot: slots[active], onKeyDown, onCellFocus };
}
