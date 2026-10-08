import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/ui/PageHeader";
import { COMPARE_MODES, COMPARE_REPORT_TYPES, compareMode, type CompareMode } from "../routes/destinations";
import { loadAnalysisTab, loadNetworkTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);
const NetworkTab = lazy(loadNetworkTab);

/** Compare: the board `compareMode` picks from `by`. */
export function CompareTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const mode = compareMode(params.get("by"));
  function setMode(next: CompareMode) {
    setParams((prev) => {
      const n = new URLSearchParams(prev);
      n.set("by", next);
      return n;
    });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* One header for both modes, so the toggle under it stays put when the
          mode switches. The agencies board titles itself as a section. */}
      <PageHeader title={t("nav.compare")} />
      <div
        role="group"
        aria-label={t("nav.compare")}
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
        {COMPARE_MODES.map(({ mode: m, labelKey }) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            style={{
              padding: "5px 12px",
              fontSize: "var(--text-sm)",
              border: "none",
              borderRadius: "var(--radius)",
              background: mode === m ? "var(--accent-soft)" : "transparent",
              color: mode === m ? "var(--accent-strong)" : "var(--text-secondary)",
              fontWeight: mode === m ? 500 : 400,
            }}
          >
            {t(labelKey)}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        {mode === "agencies" ? <NetworkTab /> : <AnalysisTab reportTypes={COMPARE_REPORT_TYPES} />}
      </div>
    </div>
  );
}
