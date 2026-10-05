import { useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { usePendingNavTarget } from "./navPendingContext";
import "./NavIndicator.css";

/** The one highlight a list of nav links shares. Put it first inside a
 *  positioned container of PendingNavLinks: it sits on the current screen's
 *  link and travels to a clicked one at once, while that screen is still
 *  loading, so the click is answered before the screen arrives. `axis="y"`
 *  fills a vertical list's row; `axis="x"` draws a line above a horizontal
 *  tab's. It moves by writing its own style, so a move re-renders nothing. */
export function NavIndicator({ axis }: { axis: "x" | "y" }) {
  const ref = useRef<HTMLSpanElement>(null);
  const { pathname } = useLocation();
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
        box.querySelector<HTMLElement>('a[aria-busy="true"]') ?? box.querySelector<HTMLElement>("a.active");
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
        indicator.style.transform = `translateX(${r.left - outer.left + box.scrollLeft}px)`;
        indicator.style.width = `${r.width}px`;
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
  }, [axis, pathname, pendingTo]);

  return <span ref={ref} className={`nav-indicator nav-indicator--${axis}`} aria-hidden="true" />;
}
