/**
 * ActivityStrip — in-context loading signal rendered beneath the header.
 *
 * Shows a soft lavender band with three pulsing dots and a translated
 * "Loading…" label whenever any mutation is in flight. Replaces the
 * 3 px TopProgressBar that lived at the top of the viewport; the
 * in-content context is where the user's attention already is.
 *
 * Gating: `useIsMutating() > 0`. `useIsFetching()` is intentionally
 * excluded — background polls (Live tab's 30 s auto-refresh) would
 * make the strip flash constantly. Mutations always correspond to a
 * user-initiated action where explicit feedback is appropriate.
 *
 * Grace period: 80 ms before showing, so cache-hit mutations don't
 * produce a visible blink. 200 ms CSS fade-out for smooth disappearance.
 *
 * Layout contract: a 24 px row is reserved at all times (via a 1 px
 * transparent border) so the content below does not shift when the strip
 * appears or disappears.
 */
import { useEffect, useState } from "react";
import { useIsMutating } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

/** Guards against duplicate <style> injection across Strict Mode double-invoke and HMR remounts. */
let _stripStylesInjected = false;

/** CSS injected once into the document head; scoped to [data-activity-strip]. */
const STRIP_CSS = `
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
`;

/**
 * Injects `STRIP_CSS` into the document `<head>` exactly once per module
 * lifetime. The module-level `_stripStylesInjected` flag survives React
 * Strict Mode's double-invocation of effects and Vite HMR remounts, both of
 * which would reset a `useRef`.
 */
function useStripStyles(): void {
  useEffect(() => {
    if (_stripStylesInjected) return;
    const el = document.createElement("style");
    el.textContent = STRIP_CSS;
    document.head.appendChild(el);
    _stripStylesInjected = true;
  }, []);
}

/**
 * Horizontal activity strip that signals in-flight mutations to the user.
 *
 * Renders an always-present 24 px row in the App shell. The row is visually
 * transparent when idle and transitions to a soft lavender band with animated
 * dots when `useIsMutating()` reports one or more active mutations.
 */
export function ActivityStrip() {
  const mutating = useIsMutating();
  const { t } = useTranslation();
  const busy = mutating > 0;
  const [visible, setVisible] = useState(false);

  useStripStyles();

  useEffect(() => {
    if (busy) {
      const id = setTimeout(() => setVisible(true), 80);
      return () => clearTimeout(id);
    }
    setVisible(false);
  }, [busy]);

  return (
    <div
      data-activity-strip
      role="status"
      aria-live="polite"
      aria-atomic="true"
      style={{
        height: 24,
        flexShrink: 0,
        background: visible ? "rgba(91, 108, 173, 0.06)" : "transparent",
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
        transition:
          "opacity 200ms ease-out, background 200ms ease-out, border-color 200ms ease-out",
        overflow: "hidden",
      }}
    >
      <span aria-hidden="true" style={{ display: "inline-flex", gap: 3 }}>
        <span className="as-dot" />
        <span className="as-dot" />
        <span className="as-dot" />
      </span>
      {visible && <span>{t("app.loading.banner")}</span>}
    </div>
  );
}
