import { useTranslation } from "react-i18next";

/**
 * One active filter, shown as a removable chip.
 *
 * Shared by every filter surface that can show a filter as *applied* rather
 * than as a control being set — the tab filter bar's dimension chips and the
 * analysis screen's service chip — so "what is currently narrowing this
 * view, and how do I drop it" looks and reads the same wherever it appears.
 * The toggle pills inside a filter popover are a different thing (a choice
 * being made, not a choice in force) and stay with `pillStyles`.
 */
export function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  const { t } = useTranslation();
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: "var(--accent-soft)",
        color: "var(--accent)",
        border: "1px solid var(--accent)",
        borderRadius: 999,
        padding: "3px 10px 3px 12px",
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      {label}
      <button
        type="button"
        onClick={onClear}
        aria-label={`${label} ${t("filters.chip_remove_suffix")}`}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          padding: 0,
          cursor: "pointer",
          fontSize: 14,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </span>
  );
}
