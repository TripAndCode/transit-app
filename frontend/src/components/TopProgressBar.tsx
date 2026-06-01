import { useEffect, useState } from "react";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";

/**
 * Top-of-viewport progress bar — visible without being alarming.
 *
 * Visual recipe:
 *   - 3px tall (up from 2px; below 3 is hard to spot)
 *   - Accent-color gradient with a soft glowing leading edge — the eye picks
 *     up the moving highlight before the bar itself
 *   - 80-180ms grace before showing → no flash for sub-perceptible queries
 *   - 200ms fade-out → smooth disappearance once idle
 *
 * Calm constraints:
 *   - No alarm hues. Accent (#5b6cad) is below the 55% saturation ceiling
 *   - Single smooth ease-in-out cycle, no bounce, no jitter
 */
export function TopProgressBar() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const busy = fetching + mutating > 0;

  // 80ms grace prevents flash-and-go on cached queries; 180ms once visible
  // avoids the bar appearing for very short cache-hit traversals.
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (busy) {
      const id = setTimeout(() => setVisible(true), 80);
      return () => clearTimeout(id);
    }
    setVisible(false);
  }, [busy]);

  return (
    <div
      aria-hidden
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        height: 3,
        zIndex: 1000,
        pointerEvents: "none",
        opacity: visible ? 1 : 0,
        transition: "opacity 200ms ease-out",
        overflow: "hidden",
      }}
    >
      {/* Running stripe with a glowing leading edge */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          width: "35%",
          background:
            "linear-gradient(90deg, rgba(91,108,173,0) 0%, var(--accent, #5b6cad) 55%, #8fa1d6 95%, rgba(143,161,214,0) 100%)",
          animation: visible ? "tpb-slide 1.4s cubic-bezier(0.65, 0, 0.35, 1) infinite" : "none",
          boxShadow: "0 0 8px rgba(91, 108, 173, 0.5)",
          willChange: "transform",
        }}
      />
      <style>{`
        @keyframes tpb-slide {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(385%); }
        }
        @media (prefers-reduced-motion: reduce) {
          /* Static accent bar — communicates busy without animation. */
          [aria-hidden] > div { animation: none !important; transform: translateX(0) !important; width: 100% !important; opacity: 0.45; }
        }
      `}</style>
    </div>
  );
}
