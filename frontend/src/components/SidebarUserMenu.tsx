import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { useConfig } from "../api/config";
import { useTheme } from "../styles/useTheme";
import { SUPPORTED_LOCALES, type Locale } from "../i18n";

const LOCALE_LABELS: Record<Locale, string> = { ja: "日本語", en: "English" }; // i18n-ignore: native locale labels render in their own language

const popItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  width: "100%",
  padding: "7px 9px",
  borderRadius: 5,
  fontSize: 12,
  color: "var(--text-secondary)",
  textDecoration: "none",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  font: "inherit",
  textAlign: "left",
};

/** Sidebar footer control: collapses what used to be five separate header
 *  controls (Live/sign-in/language/theme/settings) into one avatar-trigger
 *  popover, following the standard "user menu" pattern instead of a row of
 *  bare icons wrapping across two lines. */
export function SidebarUserMenu({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { t, i18n } = useTranslation();
  const { data: config, isLoading: configLoading } = useConfig();
  const { data: session, isLoading: sessionLoading } = useSession();
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (sessionLoading || configLoading) return null;

  const current = (i18n.resolvedLanguage ?? "ja") as Locale;
  const other = SUPPORTED_LOCALES.find((l) => l !== current) ?? current;
  const displayName = session ? session.name || session.email : t("common.guest");
  const initial = displayName.slice(0, 1).toUpperCase();

  return (
    <div ref={ref} style={{ position: "relative", margin: "4px 10px 0" }}>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: "calc(100% + 6px)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.10)",
            padding: 6,
            zIndex: 10,
          }}
        >
          {config?.auth_enabled &&
            (session ? (
              <Link role="menuitem" to="/me" onClick={() => setOpen(false)} style={popItemStyle}>
                <span>{displayName}</span>
              </Link>
            ) : (
              <Link role="menuitem" to="/login" onClick={() => setOpen(false)} style={popItemStyle}>
                <span>{t("common.login")}</span>
              </Link>
            ))}
          {config?.auth_enabled && session?.role === "admin" && (
            <Link role="menuitem" to="/admin" onClick={() => setOpen(false)} style={popItemStyle}>
              <span>{t("account.admin_link")}</span>
            </Link>
          )}
          <Link role="menuitem" to="/help" onClick={() => setOpen(false)} style={popItemStyle}>
            <span>{t("nav.help")}</span>
          </Link>
          <button type="button" role="menuitem" onClick={() => void i18n.changeLanguage(other)} style={popItemStyle}>
            <span>{t("common.language_aria")}</span>
            <span style={{ color: "var(--text-tertiary)", fontSize: 11 }}>{LOCALE_LABELS[current]}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            style={popItemStyle}
          >
            <span>{theme === "dark" ? t("common.theme_toggle_to_light") : t("common.theme_toggle_to_dark")}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
            style={popItemStyle}
          >
            <span>{t("header.settings_aria")}</span>
          </button>
        </div>
      )}
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("nav.account_menu")}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: 8,
          borderRadius: 8,
          border: "1px solid transparent",
          background: "transparent",
          cursor: "pointer",
          font: "inherit",
          color: "var(--text-primary)",
          textAlign: "left",
        }}
      >
        <span
          style={{
            width: 26,
            height: 26,
            borderRadius: "50%",
            flexShrink: 0,
            background: "var(--accent-soft)",
            color: "var(--accent)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          {initial}
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayName}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{LOCALE_LABELS[current]}</div>
        </span>
        <span
          aria-hidden
          style={{
            color: "var(--text-tertiary)",
            fontSize: 11,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 160ms ease",
          }}
        >
          &#9662;
        </span>
      </button>
    </div>
  );
}
