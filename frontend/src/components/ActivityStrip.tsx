/**
 * ActivityStrip — in-context loading signal beneath the header.
 *
 * Shows a soft lavender band with three pulsing dots and a translated
 * "Loading..." label whenever any mutation is in flight. Replaces the
 * 3px TopProgressBar that lived at the top of the viewport — the
 * in-viewport context is where the user's attention already is.
 *
 * Gating: `useIsMutating() > 0`. We intentionally do NOT use
 * `useIsFetching()` here because background polls (Live tab's 30s
 * auto-refresh) would make the strip flash constantly. Mutations
 * always correspond to a user-initiated action where they want
 * feedback.
 *
 * Grace period: 80ms before showing, so cache-hit mutations don't
 * blink. 200ms fade-out for smooth disappearance.
 */
import { useEffect, useState } from "react";
import { useIsMutating } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

export function ActivityStrip() {
  const mutating = useIsMutating();
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (mutating > 0) {
      const id = setTimeout(() => setVisible(true), 80);
      return () => clearTimeout(id);
    }
    setVisible(false);
  }, [mutating]);

  return (
    <div
      data-activity-strip
      role="status"
      aria-live="polite"
      aria-atomic="true"
      style={{
        // Reserve the row even when idle so the layout doesn't shift on
        // show/hide. 24px is enough for the dots + label without crowding.
        height: 24,
        flexShrink: 0,
        background: visible
          ? "rgba(91, 108, 173, 0.06)"
          : "transparent",
        borderBottom: visible
          ? "1px solid rgba(91, 108, 173, 0.25)"
          : "1px solid transparent",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 16px",
        fontSize: 12,
        color: "var(--accent, #5b6cad)",
        opacity: visible ? 1 : 0,
        transition: "opacity 200ms ease-out, background 200ms ease-out, border-color 200ms ease-out",
        overflow: "hidden",
      }}
    >
      <span aria-hidden="true" style={{ display: "inline-flex", gap: 3 }}>
        <span className="as-dot" />
        <span className="as-dot" />
        <span className="as-dot" />
      </span>
      <span>{t("app.loading.banner")}</span>
      <style>{`
        [data-activity-strip] .as-dot {
          display: inline-block;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent, #5b6cad);
          animation: as-pulse 1s ease-in-out infinite;
          opacity: 0.25;
        }
        [data-activity-strip] .as-dot:nth-child(2) { animation-delay: 0.15s; }
        [data-activity-strip] .as-dot:nth-child(3) { animation-delay: 0.30s; }
        @keyframes as-pulse {
          0%, 80%, 100% { opacity: 0.25; transform: scale(0.8); }
          40%           { opacity: 1;    transform: scale(1.1); }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-activity-strip] .as-dot {
            animation: none;
            opacity: 0.7;
            transform: none;
          }
        }
      `}</style>
    </div>
  );
}
