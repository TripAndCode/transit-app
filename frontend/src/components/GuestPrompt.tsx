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
      // logged in → clear timer + dismissal so a future logout starts fresh
      localStorage.removeItem(STARTED);
      localStorage.removeItem(DISMISSED);
      setShow(false);
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
    check();
    const t = setInterval(check, 30_000);
    return () => clearInterval(t);
  }, [session, isLoading]);

  if (!show) return null;
  return (
    <div
      role="status"
      style={{
        position: "sticky", top: 0, zIndex: 50,
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
        style={{ background: "transparent", border: "none", cursor: "pointer", fontSize: 16 }}
      >
        ×
      </button>
    </div>
  );
}
