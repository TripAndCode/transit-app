import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { ByModeSwitch } from "../components/ByModeSwitch";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { useByMode } from "../hooks/useByMode";
import { COMPARE_MODES, COMPARE_REPORT, compareMode } from "../routes/destinations";
import { loadSingleReportScreen, loadNetworkTab, loadSupplyMapView, loadTransfersView } from "../routes/lazyTabs";

const SingleReportScreen = lazy(loadSingleReportScreen);
const NetworkTab = lazy(loadNetworkTab);
const SupplyMapView = lazy(loadSupplyMapView);
const TransfersView = lazy(loadTransfersView);

/** Compare: the board `compareMode` picks from `by`. Transfers sit here
 *  because a pair sets two routes, usually two operators', side by side at
 *  one hub; the Transfers board reads the pairs this agency is either side of. */
export function CompareTab() {
  const { t } = useTranslation();
  const [mode, setMode] = useByMode(compareMode);
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {/* One header for every mode, so the toggle under it stays put when the
            mode switches. The agencies board titles itself as a section. */}
        <ScopePageHeader title={t("nav.compare")} />
        <ByModeSwitch label={t("nav.compare")} modes={COMPARE_MODES} current={mode} onChange={setMode} />
        <div style={{ flex: 1, minHeight: 0 }}>
          {mode === "agencies" ? (
            <NetworkTab />
          ) : mode === "supply" ? (
            <SupplyMapView />
          ) : mode === "transfers" ? (
            <TransfersView />
          ) : (
            <SingleReportScreen reportType={COMPARE_REPORT} />
          )}
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
