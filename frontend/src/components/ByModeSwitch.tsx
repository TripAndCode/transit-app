import { useTranslation } from "react-i18next";

/** The pressed-button switch between a screen's views, under its title. */
export function ByModeSwitch<M extends string>({
  label,
  modes,
  current,
  onChange,
}: {
  label: string;
  modes: readonly { mode: M; labelKey: string }[];
  current: M;
  onChange: (next: M) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={label}
      style={{
        display: "inline-flex",
        alignSelf: "flex-start",
        gap: 2,
        padding: 2,
        marginBottom: 12,
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--radius)",
        background: "var(--bg-surface)",
      }}
    >
      {modes.map(({ mode, labelKey }) => (
        <button
          key={mode}
          type="button"
          aria-pressed={current === mode}
          onClick={() => onChange(mode)}
          style={{
            padding: "5px 12px",
            fontSize: "var(--text-sm)",
            border: "none",
            borderRadius: "var(--radius)",
            background: current === mode ? "var(--accent-soft)" : "transparent",
            color: current === mode ? "var(--accent-strong)" : "var(--text-secondary)",
            fontWeight: current === mode ? 500 : 400,
          }}
        >
          {t(labelKey)}
        </button>
      ))}
    </div>
  );
}
