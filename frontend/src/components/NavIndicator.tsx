import { useLayoutEffect, useRef } from "react";
import { usePendingNavTarget } from "./navPendingContext";
import "./NavIndicator.css";

/** The one highlight a list of nav links shares. Put it first inside a
 *  positioned container of PendingNavLinks: it sits on the current screen's
 *  link and travels to a clicked one at once, while that screen is still
 *  loading, so the click is answered before the screen arrives. `axis="y"`
 *  fills a vertical list's row; `axis="x"` draws a line above a horizontal
 *  tab's. It moves by writing its own style, so a move re-renders nothing,
 *  and it glides on transform alone: the line's width is a scale, while a
 *  row's height (rows in one list rarely differ) is taken at once.
 *
 *  `watch` is whatever decides the current entry (the pathname, for route
 *  links), so the highlight moves when it changes; a list whose current
 *  entry is not a route link names it with the `current` selector. */
export function NavIndicator({
  axis,
  current = "a.active",
  watch,
  className,
}: {
  axis: "x" | "y";
  current?: string;
  watch?: unknown;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const pendingTo = usePendingNavTarget();

  useLayoutEffect(() => {
    const indicator = ref.current;
    const box = indicator?.parentElement;
    if (!indicator || !box) return;

    // It glides only from a place it was already shown in; its first place,
    // and a place after a resize, it takes at once.
    function place(glide: boolean) {
      if (!indicator || !box) return;
      const target =
        box.querySelector<HTMLElement>('a[aria-busy="true"]') ?? box.querySelector<HTMLElement>(current);
      if (!target) {
        indicator.style.opacity = "0";
        delete indicator.dataset.glide;
        return;
      }
      if (!glide) delete indicator.dataset.glide;
      const outer = box.getBoundingClientRect();
      const r = target.getBoundingClientRect();
      if (axis === "y") {
        indicator.style.transform = `translateY(${r.top - outer.top + box.scrollTop}px)`;
        indicator.style.height = `${r.height}px`;
      } else {
        // A 1px line scaled to the tab's width: the glide stays on transform.
        indicator.style.transform = `translateX(${r.left - outer.left + box.scrollLeft}px) scaleX(${r.width})`;
      }
      indicator.style.opacity = "1";
      if (indicator.dataset.glide == null) {
        // Commit this position before transitions apply, so it isn't animated.
        void indicator.offsetWidth;
        indicator.dataset.glide = "";
      }
    }

    place(true);
    const observer = new ResizeObserver(() => place(false));
    observer.observe(box);
    return () => observer.disconnect();
  }, [axis, current, watch, pendingTo]);

  return (
    <span
      ref={ref}
      className={`nav-indicator nav-indicator--${axis}${className ? ` ${className}` : ""}`}
      aria-hidden="true"
    />
  );
}
