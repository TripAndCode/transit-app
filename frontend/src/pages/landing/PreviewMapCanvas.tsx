import { useRef, type CSSProperties } from "react";
import { useCityMapAnimation } from "./useCityMapAnimation";

/** The Map panel's animated backdrop: the same schematic city scene as the
 *  hero, but upright (no tilt/perspective) and sized to fill its full-bleed
 *  container -- matching the real `MapTab`'s own
 *  `position:absolute; inset:0` map canvas, not a static screenshot.
 *  `filterCss` is applied as a CSS `filter` on the canvas element itself so
 *  the style/heatmap-field toggles in `PreviewMapPanel` can visibly change
 *  the scene without this component (or the shared drawing code) needing to
 *  know about either toggle. */
export function PreviewMapCanvas({ filterCss }: { filterCss: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useCityMapAnimation(canvasRef);

  const style: CSSProperties = {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    display: "block",
    filter: filterCss,
    transition: "filter var(--transition)",
  };

  return <canvas ref={canvasRef} aria-hidden="true" style={style} />;
}
