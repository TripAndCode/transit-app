import { useTranslation } from "react-i18next";

export type AskMode = "chat" | "build";

type Props = {
  value: AskMode;
  onChange: (next: AskMode) => void;
};

const MODES: AskMode[] = ["chat", "build"];
const MODE_ICONS: Record<AskMode, string> = {
  chat: "💬",
  build: "🛠",
};

export function AskModeToggle({ value, onChange }: Props) {
  const { t } = useTranslation();

  return (
    <div
      role="group"
      aria-label={t("ask.mode.label")}
      style={{
        display: "inline-flex",
        gap: "var(--space-1)",
        padding: "3px",
        background: "var(--bg-soft)",
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border-subtle)",
      }}
    >
      {MODES.map((mode) => {
        const isActive = value === mode;
        return (
          <button
            key={mode}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(mode)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-1)",
              padding: "5px 14px",
              borderRadius: "var(--radius)",
              border: "none",
              background: isActive ? "var(--accent)" : "transparent",
              color: isActive ? "#ffffff" : "var(--text-secondary)",
              fontSize: 13,
              fontWeight: isActive ? 600 : 400,
              lineHeight: 1.4,
              transition:
                "background var(--transition), color var(--transition), font-weight var(--transition)",
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            <span aria-hidden="true">{MODE_ICONS[mode]}</span>
            {t(`ask.mode.${mode}`)}
          </button>
        );
      })}
    </div>
  );
}
