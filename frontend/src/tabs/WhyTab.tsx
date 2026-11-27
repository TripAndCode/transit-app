import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { ByModeSwitch } from "../components/ByModeSwitch";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { useByMode } from "../hooks/useByMode";
import { WHY_MODES, WHY_REPORT, whyMode } from "../routes/destinations";
import { loadSingleReportScreen, loadSlowZonesView } from "../routes/lazyTabs";

const SingleReportScreen = lazy(loadSingleReportScreen);
const SlowZonesView = lazy(loadSlowZonesView);

/** Why: the view `whyMode` picks from `by`. */
export function WhyTab() {
  const { t } = useTranslation();
  const [mode, setMode] = useByMode(whyMode);
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.why")} />
        <ByModeSwitch label={t("nav.why")} modes={WHY_MODES} current={mode} onChange={setMode} />
        <div style={{ flex: 1, minHeight: 0 }}>
          {mode === "segments" ? <SlowZonesView /> : <SingleReportScreen reportType={WHY_REPORT} />}
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
