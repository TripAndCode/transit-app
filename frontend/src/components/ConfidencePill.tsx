import { useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  confidence: number; // 0..1
  toolName: string;   // e.g. "top_n"
  argsPreview?: string; // e.g. "metric=avg_delay, n=10"
  onEdit: () => void;
};

/**
 * Renders a confidence badge after an assistant message.
 *
 * - confidence >= 0.7 (high): collapsed pill, click to expand, no "Wrong?" link.
 * - 0.5 <= confidence < 0.7 (medium): always-expanded, shows "Wrong?" link.
 * - confidence < 0.5: NOT rendered here — AskTab renders the block-before-execute card.
 */
export function ConfidencePill({ confidence, toolName, argsPreview, onEdit }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (confidence < 0.5) return null;

  const isHigh = confidence >= 0.7;

  if (isHigh) {
    return (
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          marginTop: 6,
          padding: "3px 10px",
          background: "var(--bg-soft)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 999,
          fontSize: 12,
          color: "var(--text-secondary)",
          cursor: "pointer",
          userSelect: "none",
        }}
        role="button"
        aria-expanded={expanded}
        tabIndex={0}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
      >
        <span style={{ opacity: 0.6 }}>{t("ask.pill.interpreted")}</span>
        <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{toolName}</span>
        {expanded && argsPreview && (
          <span style={{ color: "var(--text-tertiary)", fontSize: 11, marginLeft: 4 }}>
            {argsPreview}
          </span>
        )}
        <span style={{ opacity: 0.4, fontSize: 10 }}>{expanded ? "▲" : "▼"}</span>
      </div>
    );
  }

  // medium confidence (0.5 <= confidence < 0.7) — always expanded with "Wrong?" link
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 6,
        marginTop: 6,
        padding: "4px 10px",
        background: "var(--bg-soft)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 999,
        fontSize: 12,
        color: "var(--text-secondary)",
      }}
    >
      <span style={{ opacity: 0.6 }}>{t("ask.pill.interpreted")}</span>
      <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{toolName}</span>
      {argsPreview && (
        <span style={{ color: "var(--text-tertiary)", fontSize: 11 }}>{argsPreview}</span>
      )}
      <button
        type="button"
        onClick={onEdit}
        style={{
          background: "transparent",
          border: "none",
          padding: "0 2px",
          fontSize: 12,
          color: "var(--accent)",
          cursor: "pointer",
          textDecoration: "underline",
          textUnderlineOffset: 2,
        }}
      >
        {t("ask.pill.wrong")}
      </button>
    </div>
  );
}
