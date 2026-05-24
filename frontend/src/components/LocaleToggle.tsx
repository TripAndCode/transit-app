import { useTranslation } from "react-i18next";
import type { CSSProperties } from "react";

import { SUPPORTED_LOCALES, type Locale } from "../i18n";

const buttonStyle = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "transparent",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: "1px solid var(--border-soft)",
  borderRadius: 4,
  padding: "3px 8px",
  fontSize: 12,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
});

const LABELS: Record<Locale, string> = { ja: "日本語", en: "English" };

export function LocaleToggle() {
  const { i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? "ja") as Locale;
  return (
    <div role="group" aria-label="Language" style={{ display: "inline-flex", gap: 4 }}>
      {SUPPORTED_LOCALES.map((lng) => (
        <button
          key={lng}
          type="button"
          onClick={() => void i18n.changeLanguage(lng)}
          style={buttonStyle(current === lng)}
          aria-pressed={current === lng}
        >
          {LABELS[lng]}
        </button>
      ))}
    </div>
  );
}
