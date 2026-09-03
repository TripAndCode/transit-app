import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

/** Surfaces the User Manual inside the dashboard-preview shell's content
 *  column the same way the real `HelpHint` does app-wide: a small
 *  dismissable banner, not a peer sidebar tab. Reuses the real
 *  `help.hint_*` copy verbatim. Unlike the real component (a
 *  `position: fixed` viewport corner pill with a first-visit delay/timeout),
 *  this is pinned to the *content column's own* box and shows immediately --
 *  the shell is already a bounded demo surface, not the persistent app
 *  chrome `HelpHint`'s delay/timeout logic exists to avoid competing with.
 *  Anchored bottom-right (not bottom-left, like the real one) so it never
 *  overlaps the Map panel's own bottom-left floating controls. The link
 *  itself is real: `/help` renders outside auth, exactly like a visitor
 *  clicking it here would get. */
export function PreviewHelpHint() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  return (
    <div
      role="status"
      style={{
        position: "absolute",
        right: 12,
        bottom: 12,
        zIndex: 2,
        display: "flex",
        alignItems: "center",
        gap: 10,
        maxWidth: 320,
        padding: "10px 12px",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
        fontSize: 12.5,
      }}
    >
      <span aria-hidden="true">💡</span>
      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{t("help.hint_text")}</span>
      <Link
        to="/help"
        style={{ color: "var(--accent)", fontWeight: 600, textDecoration: "none", whiteSpace: "nowrap" }}
      >
        {t("help.hint_cta")}
      </Link>
      <button
        type="button"
        aria-label={t("help.hint_dismiss_aria")}
        onClick={() => setDismissed(true)}
        style={{ background: "transparent", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 15, lineHeight: 1, padding: 0 }}
      >
        ×
      </button>
    </div>
  );
}
