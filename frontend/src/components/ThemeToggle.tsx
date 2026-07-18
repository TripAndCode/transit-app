import { useTranslation } from "react-i18next";
import { useTheme } from "../styles/useTheme";

export function ThemeToggle() {
  const { t } = useTranslation();
  const [theme, setTheme] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label = theme === "dark" ? t("common.theme_toggle_to_light") : t("common.theme_toggle_to_dark");

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={label}
      title={label}
      style={{
        background: "transparent",
        border: "none",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-secondary)",
        cursor: "pointer",
        padding: 4,
      }}
    >
      {theme === "dark" ? (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="8" cy="8" r="3.5" />
          <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M13.5 9.5A6 6 0 1 1 6.5 2.5a5 5 0 0 0 7 7z" />
        </svg>
      )}
    </button>
  );
}
