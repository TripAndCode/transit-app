import { useRef } from "react";
import { useCityMapAnimation } from "./useCityMapAnimation";

/** Animated schematic city map: procedurally-drawn blocks/park/river with
 *  two metro-style routes (on-time vs. elevated-delay, colored from real
 *  theme tokens) and small vehicle markers looping along them. Purely
 *  decorative (`aria-hidden`) -- a screen reader gets the real headline
 *  text next to it, not a description of the animation. The tilted 3D
 *  presentation lives entirely in this wrapper's CSS
 *  (`.landing-hero__scene-tilt`); the animation loop itself is shared with
 *  the dashboard-preview Map panel via `useCityMapAnimation`. */
export function CityMapHero() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  useCityMapAnimation(canvasRef);

  return (
    <div className="landing-hero__scene-tilt">
      <canvas ref={canvasRef} className="landing-hero__canvas" aria-hidden="true" />
    </div>
  );
}
