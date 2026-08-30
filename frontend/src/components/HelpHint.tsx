import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Z_INDEX } from "../styles/zIndex";

const FIRST_VISIT_KEY = "help_hint_first_visit_at";
const DISMISSED_KEY = "help_hint_dismissed";
const SHOW_AFTER_MS = 3_000;
const HIDE_AFTER_MS = 5 * 60 * 1000;

/**
 * One-time, dismissable hint pointing a brand-new visitor at the User Manual
 * (`/help`) — today it's only reachable via the account-menu popover
 * (`SidebarUserMenu`), which nothing nudges a first-time user toward. Unlike
 * `GuestPrompt` (a recurring engagement nudge that re-appears after a
 * dismissal), this is a one-time orientation aid: once dismissed, or once the
 * first-visit window has passed, it never shows again for this browser. Not
 * gated on anonymous/logged-in status — the Help page is equally useful to
 * both, and restricting it would add complexity with no clear benefit.
 *
 * Rendered as a small fixed corner pill rather than a fourth top banner —
 * DataStalenessBanner/FeedHealthBanner/GuestPrompt already stack up to three
 * deep above the content, and this is an orientation aid, not a persistent
 * warning or conversion nudge, so it shouldn't compete for that same space.
 */
export function HelpHint() {
  const { t } = useTranslation();
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (localStorage.getItem(DISMISSED_KEY)) return;

    let firstVisit = parseInt(localStorage.getItem(FIRST_VISIT_KEY) || "0", 10);
    if (!firstVisit) {
      firstVisit = Date.now();
      localStorage.setItem(FIRST_VISIT_KEY, String(firstVisit));
    }

    const elapsed = Date.now() - firstVisit;
    const remaining = HIDE_AFTER_MS - elapsed;
    // Not enough of the first-visit window left for the show-delay to still
    // make sense (e.g. a page reload seconds before the window closes) —
    // skip rather than flash the hint on and immediately back off.
    if (remaining <= SHOW_AFTER_MS) return;

    // Deferred through a timer (never fires synchronously in the effect) so
    // this never trips the React Compiler's set-state-in-effect rule —
    // same pattern GuestPrompt already uses.
    const showTimer = setTimeout(() => setShow(true), SHOW_AFTER_MS);
    const hideTimer = setTimeout(() => setShow(false), remaining);
    return () => {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
    };
  }, []);

  if (!show) return null;

  function dismiss() {
    localStorage.setItem(DISMISSED_KEY, "1");
    setShow(false);
  }

  return (
    <div
      role="status"
      style={{
        position: "fixed",
        left: 16,
        bottom: 16,
        zIndex: Z_INDEX.drawerBackdrop - 1,
        display: "flex",
        alignItems: "center",
        gap: 10,
        maxWidth: "min(320px, calc(100vw - 32px))",
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
        onClick={dismiss}
        style={{
          color: "var(--accent)",
          fontWeight: 600,
          textDecoration: "none",
          whiteSpace: "nowrap",
        }}
      >
        {t("help.hint_cta")}
      </Link>
      <button
        type="button"
        aria-label={t("help.hint_dismiss_aria")}
        onClick={dismiss}
        style={{
          background: "transparent",
          border: "none",
          color: "var(--text-tertiary)",
          cursor: "pointer",
          fontSize: 15,
          lineHeight: 1,
          padding: 0,
        }}
      >
        ×
      </button>
    </div>
  );
}
