import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";

const STARTED = "guest_started_at";
const DISMISSED = "guest_dismissed_at";
const NUDGE_AFTER_MS = 10 * 60 * 1000;
const RE_NUDGE_AFTER_MS = 24 * 60 * 60 * 1000;

/** Sticky banner that nudges anonymous users to log in after 10 min of use. */
export function GuestPrompt() {
  const { t } = useTranslation();
  const { data: session, isLoading } = useSession();
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (isLoading) return;
    if (session) {
      // logged in → clear timer + dismissal so a future logout starts fresh.
      // Visibility is handled by the render guard below — no setState needed.
      localStorage.removeItem(STARTED);
      localStorage.removeItem(DISMISSED);
      return;
    }
    let started = parseInt(localStorage.getItem(STARTED) || "0", 10);
    if (!started) {
      started = Date.now();
      localStorage.setItem(STARTED, String(started));
    }

    function check() {
      const now = Date.now();
      const dismissed = parseInt(localStorage.getItem(DISMISSED) || "0", 10);
      const dismissedRecently = dismissed && now - dismissed < RE_NUDGE_AFTER_MS;
      if (now - started >= NUDGE_AFTER_MS && !dismissedRecently) {
        setShow(true);
      } else {
        setShow(false);
      }
    }
    // First check goes through a 0ms timer so the effect itself never calls
    // setState synchronously (React Compiler set-state-in-effect rule).
    const t0 = setTimeout(check, 0);
    const t = setInterval(check, 30_000);
    return () => {
      clearTimeout(t0);
      clearInterval(t);
    };
  }, [session, isLoading]);

  if (!show || isLoading || session) return null;
  return (
    <div
      role="status"
      style={{
        // Deliberately not `position: sticky` — this is an engagement nudge,
        // not a data-quality warning, so it shouldn't out-rank
        // DataStalenessBanner/FeedHealthBanner by staying pinned in view
        // while those scroll away with the rest of the content.
        padding: "8px 16px", background: "var(--surface-2)",
        display: "flex", alignItems: "center", gap: 12, fontSize: 13,
      }}
    >
      <span style={{ flex: 1 }}>{t("account.guest_prompt")}</span>
      <Link to="/login" style={{ color: "inherit", padding: "4px 12px",
                                  background: "var(--surface-1)", borderRadius: 4,
                                  textDecoration: "none" }}>
        {t("common.login")}
      </Link>
      <button
        aria-label={t("common.close")}
        onClick={() => {
          localStorage.setItem(DISMISSED, String(Date.now()));
          setShow(false);
        }}
        style={{ background: "transparent", color: "inherit", border: "none", cursor: "pointer", fontSize: 16 }}
      >
        ×
      </button>
    </div>
  );
}
