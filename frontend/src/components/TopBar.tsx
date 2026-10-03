import { Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { useMediaQuery, COARSE_POINTER_QUERY } from "../hooks/useMediaQuery";
import { openCommandPalette } from "./commandPaletteEvents";
import { DataFreshness } from "./DataFreshness";

/** The bar above every agency screen: one search-and-ask field that opens
 *  the command palette, and how recent the agency's data is. */
export function TopBar() {
  const { t } = useTranslation();
  const coarsePointer = useMediaQuery(COARSE_POINTER_QUERY);
  const id = useAgencyId();
  const { data: agencies } = useAgencies();
  const through = agencies?.find((a) => a.agency_id === id)?.latest_data_date ?? null;
  return (
    <div
      className="app-topbar"
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "8px 16px",
        padding: "10px clamp(16px, 4vw, 24px)",
        borderBottom: "1px solid var(--border-soft)",
        background: "var(--bg-surface)",
      }}
    >
      <button
        type="button"
        data-tour="ask-nav"
        aria-keyshortcuts="Meta+K Control+K"
        onClick={() => openCommandPalette()}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flex: "1 1 280px",
          maxWidth: 560,
          minWidth: 0,
          height: 38,
          padding: "0 10px 0 12px",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          background: "var(--bg-page)",
          color: "var(--text-tertiary)",
          fontSize: "var(--text-sm)",
          textAlign: "left",
          cursor: "text",
        }}
      >
        <Search size={16} strokeWidth={1.75} aria-hidden="true" style={{ flexShrink: 0 }} />
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t("topbar.ask_placeholder")}
        </span>
        {!coarsePointer && (
        <span aria-hidden="true" style={{ display: "flex", gap: 3 }}>
          {["⌘", "K"].map((k) => (
            <kbd
              key={k}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 4,
                padding: "0 5px",
                background: "var(--bg-surface)",
                color: "var(--text-secondary)",
              }}
            >
              {k}
            </kbd>
          ))}
        </span>
        )}
      </button>
      {id != null && through && <DataFreshness agencyId={id} through={through} />}
    </div>
  );
}
