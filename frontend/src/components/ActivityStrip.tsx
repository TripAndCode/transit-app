/**
 * ActivityStrip — in-context loading signal rendered beneath the header.
 *
 * Shows a soft accent-tinted band with three pulsing dots and a translated
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
 * The strip is rendered inside the in-flow app notice stack. It reserves no
 * space while idle, since it's `display: none`, but once mutations start it
 * takes its own row and pushes the active tab down like any other sibling.
 */
import { useEffect, useState } from "react";
import { useIsMutating } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import "./ActivityStrip.css";

/**
 * Horizontal activity strip that signals in-flight mutations to the user.
 *
 * Renders an always-present 24 px row in the App shell. The row is visually
 * transparent when idle and transitions to a soft --accent-soft band with
 * animated dots when `useIsMutating()` reports one or more active mutations.
 * Themed through --accent/--accent-soft (not a hardcoded colour) so the band
 * follows the active theme instead of always rendering the light-mode tint.
 */
export function ActivityStrip() {
  const mutating = useIsMutating();
  const { t } = useTranslation();
  const busy = mutating > 0;
  const [visible, setVisible] = useState(false);

  // Show is debounced 80ms so sub-frame mutations never flash the strip;
  // hide goes through a 0ms timeout too, keeping the effect free of
  // synchronous setState (React Compiler set-state-in-effect rule).
  useEffect(() => {
    const id = setTimeout(() => setVisible(busy), busy ? 80 : 0);
    return () => clearTimeout(id);
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
        background: visible ? "var(--accent-soft)" : "transparent",
        borderBottom: visible
          ? "1px solid color-mix(in srgb, var(--accent) 25%, transparent)"
          : "1px solid transparent",
        display: visible ? "flex" : "none",
        alignItems: "center",
        gap: 10,
        padding: "0 16px",
        fontSize: 12,
        color: "var(--accent)",
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
