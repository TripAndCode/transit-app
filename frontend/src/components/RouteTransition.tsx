import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

type Props = {
  style?: CSSProperties;
  children: ReactNode;
};

/**
 * The shell's route-enter fade.
 *
 * The wrapper deliberately outlives the route: keying it by pathname would
 * remount the routed subtree on every navigation, which is exactly what the
 * surrounding `startTransition` + single Suspense boundary exist to avoid.
 * So the animation is restarted imperatively instead — remove the class, read
 * a layout property to flush the removal, add it back.
 *
 * Under `prefers-reduced-motion: reduce` the class has no animation attached
 * at all (the rule lives inside the motion-allowed block in `global.css`), so
 * this runs but paints nothing.
 */
export function RouteTransition({ style, children }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.classList.remove("route-enter");
    void el.offsetWidth;
    el.classList.add("route-enter");
  }, [pathname]);

  return (
    <div ref={ref} className="route-enter" style={style}>
      {children}
    </div>
  );
}
