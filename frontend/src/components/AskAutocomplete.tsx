import { useEffect, useState } from "react";
import { useAskSuggest } from "../api/hooks";

type Props = {
  agencyId: number;
  q: string;
  onPick: (question: string) => void;
  onDismiss: () => void;
};

export function AskAutocomplete({ agencyId, q, onPick, onDismiss }: Props) {
  const [activeIndex, setActiveIndex] = useState(0);
  const { data } = useAskSuggest(q, agencyId);

  const isVisible = q.trim().length >= 2 && !!data && data.length > 0;

  // Reset active index when suggestions change
  useEffect(() => {
    setActiveIndex(0);
  }, [data]);

  // Keyboard navigation via window-level keydown listener
  useEffect(() => {
    if (!isVisible) return;

    const items = data!;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % items.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + items.length) % items.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        onPick(items[activeIndex].question);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onDismiss();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isVisible, data, activeIndex, onPick, onDismiss]);

  if (!isVisible) return null;

  return (
    <ul
      role="listbox"
      style={{
        position: "absolute",
        top: "100%",
        left: 0,
        right: 0,
        zIndex: 100,
        margin: "2px 0 0",
        padding: 0,
        listStyle: "none",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius)",
        boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
        overflow: "hidden",
      }}
    >
      {data!.map((item, i) => (
        <li
          key={i}
          role="option"
          aria-selected={i === activeIndex}
          onClick={() => onPick(item.question)}
          onMouseEnter={() => setActiveIndex(i)}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 14px",
            cursor: "pointer",
            fontSize: 14,
            lineHeight: 1.5,
            background: i === activeIndex ? "var(--accent-soft)" : "transparent",
            color: "var(--text-primary)",
            transition: "background var(--transition)",
          }}
        >
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {item.question}
          </span>
          <span
            style={{
              marginLeft: 12,
              fontSize: 11,
              color: "var(--text-tertiary)",
              background: "var(--bg-soft)",
              borderRadius: 4,
              padding: "1px 6px",
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {item.tool}
          </span>
        </li>
      ))}
    </ul>
  );
}
