import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { useAgencyId } from "../api/useAgencyId";
import { ByModeSwitch } from "../components/ByModeSwitch";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { useByMode } from "../hooks/useByMode";
import { ROUTES_MODES, routesMode } from "../routes/destinations";
import { loadReportCardsView } from "../routes/lazyTabs";
import { RouteTableScreen } from "./RouteTableScreen";

const ReportCardsView = lazy(loadReportCardsView);

/** The Routes list: every route in one sortable table, each row of which
 *  opens its route, or by `by=report_cards` every route's monthly grade. */
export function RoutesIndex() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [mode, setMode] = useByMode(routesMode);
  if (id == null) return null;
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.routes")} />
        <ByModeSwitch label={t("nav.routes")} modes={ROUTES_MODES} current={mode} onChange={setMode} />
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* key={id}: an agency switch starts the table at its default length. */}
          {mode === "report_cards" ? <ReportCardsView /> : <RouteTableScreen key={id} />}
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
