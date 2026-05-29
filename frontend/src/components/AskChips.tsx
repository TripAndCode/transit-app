import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAskSuggest } from "../api/hooks";
import { Skeleton } from "./Skeleton";

type Props = {
  agencyId: number;
  onPick: (question: string) => void;
};

const INITIAL_MAX = 6;
const EXPANDED_MAX = 12;

export function AskChips({ agencyId, onPick }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading } = useAskSuggest("", agencyId);

  if (isLoading) {
    return (
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          padding: "var(--space-2) 0",
        }}
      >
        {[80, 120, 100, 90, 110, 70].map((w, i) => (
          <Skeleton key={i} width={w} height={32} />
        ))}
      </div>
    );
  }

  if (!data || data.length === 0) return null;

  const limit = expanded ? EXPANDED_MAX : INITIAL_MAX;
  const visible = data.slice(0, limit);
  const hasMore = !expanded && data.length > INITIAL_MAX;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "var(--space-2)",
        padding: "var(--space-2) 0",
      }}
    >
      {visible.map((item, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onPick(item.question)}
          style={{
            background: "var(--accent-soft)",
            color: "var(--accent)",
            border: "1px solid transparent",
            borderRadius: 999,
            padding: "5px 14px",
            fontSize: 13,
            lineHeight: 1.4,
            transition: "background var(--transition), border-color var(--transition)",
            cursor: "pointer",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
          }}
        >
          {item.question}
        </button>
      ))}
      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          style={{
            background: "var(--bg-soft)",
            color: "var(--text-secondary)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 999,
            padding: "5px 14px",
            fontSize: 13,
            lineHeight: 1.4,
            cursor: "pointer",
            transition: "background var(--transition)",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--border-subtle)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft)";
          }}
        >
          {t("ask.chips.more")}
        </button>
      )}
    </div>
  );
}
