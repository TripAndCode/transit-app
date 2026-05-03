import { useIsFetching, useIsMutating } from "@tanstack/react-query";

/**
 * Slim 2px bar at the top of the viewport that animates while any TanStack
 * Query fetch or mutation is in flight. Calm: muted indigo, no flash, no
 * bounce. When idle, the bar hides entirely.
 */
export function TopProgressBar() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const active = fetching + mutating > 0;

  return (
    <div
      aria-hidden
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        height: 2,
        zIndex: 1000,
        pointerEvents: "none",
        opacity: active ? 1 : 0,
        transition: "opacity 200ms ease-out",
      }}
    >
      <div
        style={{
          width: "30%",
          height: "100%",
          background: "var(--accent)",
          animation: active ? "tpb-slide 1.2s ease-in-out infinite" : "none",
        }}
      />
      <style>{`
        @keyframes tpb-slide {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(440%); }
        }
      `}</style>
    </div>
  );
}
