import { useTranslation } from "react-i18next";
import type { ChipCategory, ChipTemplate } from "../api/types";
import { useChipCatalog, usePopularChips } from "../api/hooks";
import { Skeleton } from "./Skeleton";

type Props = {
  agencyId: number;
  onSelect: (chip: ChipTemplate) => void;
  onOpenBuilder: () => void;
  /** Highlight this chip as "staged" (tapped but not yet committed). */
  stagedChipId?: string | null;
};

type SectionDef = {
  key: string;
  emoji: string;
  category: ChipCategory;
};

const SECTIONS: SectionDef[] = [
  { key: "section_meta",    emoji: "📋", category: "meta"    },
  { key: "section_ranking", emoji: "🏆", category: "ranking" },
  { key: "section_trend",   emoji: "📈", category: "trend"   },
  { key: "section_compare", emoji: "⚖️", category: "compare" },
  { key: "section_detail",  emoji: "🚏", category: "detail"  },
];

function ChipButton({
  label,
  onClick,
  selected = false,
}: {
  label: string;
  onClick: () => void;
  selected?: boolean;
}) {
  const baseBg = selected ? "var(--accent, #4a8aaa)" : "rgba(0,0,0,0.06)";
  const baseColor = selected ? "white" : "var(--text-primary, #1a1a1a)";
  const baseBorder = selected ? "1px solid var(--accent, #4a8aaa)" : "1px solid transparent";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      style={{
        background: baseBg,
        color: baseColor,
        border: baseBorder,
        borderRadius: 999,
        padding: "5px 14px",
        fontSize: 13,
        lineHeight: 1.4,
        cursor: "pointer",
        transition: "background var(--transition, 120ms ease), box-shadow var(--transition, 120ms ease)",
        boxShadow: selected ? "0 1px 4px rgba(0,0,0,0.18)" : "none",
      }}
      onMouseEnter={(e) => {
        if (selected) return;
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = "rgba(0,0,0,0.10)";
        el.style.boxShadow = "0 1px 4px rgba(0,0,0,0.12)";
      }}
      onMouseLeave={(e) => {
        if (selected) return;
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = "rgba(0,0,0,0.06)";
        el.style.boxShadow = "none";
      }}
    >
      {label}
    </button>
  );
}

function ChipRow({
  chips,
  onSelect,
  stagedChipId,
}: {
  chips: ChipTemplate[];
  onSelect: (chip: ChipTemplate) => void;
  stagedChipId?: string | null;
}) {
  if (chips.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "var(--space-2, 6px)",
      }}
    >
      {chips.map((chip) => (
        <ChipButton
          key={chip.id}
          label={chip.title}
          onClick={() => onSelect(chip)}
          selected={stagedChipId === chip.id}
        />
      ))}
    </div>
  );
}

function Section({
  emoji,
  label,
  chips,
  onSelect,
  stagedChipId,
}: {
  emoji: string;
  label: string;
  chips: ChipTemplate[];
  onSelect: (chip: ChipTemplate) => void;
  stagedChipId?: string | null;
}) {
  if (chips.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2, 6px)" }}>
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text-secondary, #666)",
          letterSpacing: "0.03em",
        }}
      >
        {emoji} {label}
      </span>
      <ChipRow chips={chips} onSelect={onSelect} stagedChipId={stagedChipId} />
    </div>
  );
}

export function ChipCatalog({ agencyId, onSelect, onOpenBuilder, stagedChipId }: Props) {
  const { t } = useTranslation();

  const catalogQuery = useChipCatalog(agencyId);
  const popularQuery = usePopularChips(agencyId, 6);

  const isLoading = catalogQuery.isLoading || popularQuery.isLoading;
  const isError = catalogQuery.isError && popularQuery.isError;

  if (isLoading) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-4, 16px)",
          padding: "var(--space-2, 6px) 0",
        }}
      >
        {/* Skeleton rows */}
        {[0, 1, 2].map((i) => (
          <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2, 6px)" }}>
            {[80, 110, 90, 120, 100].map((w, j) => (
              <Skeleton key={j} width={w} height={32} />
            ))}
          </div>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: "var(--space-3, 10px)",
          padding: "var(--space-2, 6px) 0",
          color: "var(--text-secondary, #666)",
          fontSize: 13,
        }}
      >
        <span>{t("ask.chip_catalog.error")}</span>
        <button
          type="button"
          onClick={() => {
            catalogQuery.refetch();
            popularQuery.refetch();
          }}
          style={{
            background: "var(--bg-soft, rgba(0,0,0,0.04))",
            color: "var(--text-primary, #1a1a1a)",
            border: "1px solid var(--border-subtle, rgba(0,0,0,0.12))",
            borderRadius: 999,
            padding: "5px 14px",
            fontSize: 13,
            cursor: "pointer",
            transition: "background var(--transition, 120ms ease)",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "var(--border-subtle, rgba(0,0,0,0.12))";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "var(--bg-soft, rgba(0,0,0,0.04))";
          }}
        >
          {t("ask.chip_catalog.retry")}
        </button>
      </div>
    );
  }

  const schema = catalogQuery.data;
  const popularChips = popularQuery.data ?? [];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-4, 16px)",
        padding: "var(--space-2, 6px) 0",
      }}
    >
      {/* ⭐ よく使う — only when non-empty */}
      {popularChips.length > 0 && (
        <Section
          emoji="⭐"
          label={t("ask.chip_catalog.section_popular")}
          chips={popularChips}
          onSelect={onSelect}
          stagedChipId={stagedChipId}
        />
      )}

      {/* Category sections */}
      {schema &&
        SECTIONS.map(({ key, emoji, category }) => (
          <Section
            key={category}
            emoji={emoji}
            label={t(`ask.chip_catalog.${key}`)}
            chips={schema.chips[category] ?? []}
            onSelect={onSelect}
            stagedChipId={stagedChipId}
          />
        ))}

      {/* ＋ 組み立て */}
      <div style={{ paddingTop: "var(--space-1, 2px)" }}>
        <button
          type="button"
          onClick={onOpenBuilder}
          style={{
            background: "transparent",
            color: "var(--accent, #0070f3)",
            border: "1px dashed var(--accent, #0070f3)",
            borderRadius: 999,
            padding: "5px 14px",
            fontSize: 13,
            lineHeight: 1.4,
            cursor: "pointer",
            transition: "background var(--transition, 120ms ease)",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "var(--accent-soft, rgba(0,112,243,0.06))";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
          }}
        >
          ＋ {t("ask.chip_catalog.open_builder")}
        </button>
      </div>
    </div>
  );
}
