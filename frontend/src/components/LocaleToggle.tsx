import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LOCALES, type Locale } from "../i18n";

const LABELS: Record<Locale, string> = { ja: "日本語", en: "English" }; // i18n-ignore: native locale labels render in their own language

const triggerStyle: CSSProperties = {
  background: "transparent",
  color: "var(--text-secondary)",
  border: "none",
  padding: 0,
  fontSize: 12,
  fontWeight: 400,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
};

const menuStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  right: 0,
  zIndex: 50,
  minWidth: 120,
  background: "var(--bg-surface)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-md, 4px)",
  boxShadow: "0 4px 16px rgba(0,0,0,0.10)",
  padding: 4,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const optionStyle = (selected: boolean): CSSProperties => ({
  background: selected ? "var(--accent-soft)" : "transparent",
  color: selected ? "var(--accent)" : "var(--text-primary)",
  border: "none",
  borderRadius: 4,
  padding: "6px 10px",
  fontSize: 13,
  fontWeight: selected ? 600 : 400,
  textAlign: "left",
  cursor: "pointer",
});

export function LocaleToggle() {
  const { t, i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? "ja") as Locale;
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
        // Return focus to the trigger so keyboard users don't lose context.
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

  function select(lng: Locale) {
    void i18n.changeLanguage(lng);
    setOpen(false);
    // Return focus to the trigger so keyboard users don't lose context.
    triggerRef.current?.focus();
  }

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={triggerStyle}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{LABELS[current]}</span>
        <span aria-hidden style={{ opacity: 0.7 }}>▾</span>
      </button>
      {open && (
        <div role="listbox" aria-label={t("common.language_aria")} style={menuStyle}>
          {SUPPORTED_LOCALES.map((lng) => (
            <button
              key={lng}
              type="button"
              role="option"
              aria-selected={current === lng}
              onClick={() => select(lng)}
              style={optionStyle(current === lng)}
            >
              {LABELS[lng]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
