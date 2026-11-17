import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { WHY_MODES, WHY_REPORT, whyMode, type WhyMode } from "../routes/destinations";
import { loadSingleReportScreen, loadSlowZonesView } from "../routes/lazyTabs";

const SingleReportScreen = lazy(loadSingleReportScreen);
const SlowZonesView = lazy(loadSlowZonesView);

/** Why: the view `whyMode` picks from `by`. */
export function WhyTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const mode = whyMode(params.get("by"));
  function setMode(next: WhyMode) {
    setParams((prev) => {
      const n = new URLSearchParams(prev);
      n.set("by", next);
      return n;
    });
  }
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.why")} />
        <div
          role="group"
          aria-label={t("nav.why")}
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
          {WHY_MODES.map(({ mode: m, labelKey }) => (
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
          {mode === "segments" ? <SlowZonesView /> : <SingleReportScreen reportType={WHY_REPORT} />}
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
