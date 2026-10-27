import { useTranslation } from "react-i18next";
import { useAgencyId } from "../api/useAgencyId";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { RouteTableScreen } from "./RouteTableScreen";

/** The Routes list: every route in one sortable table, each row of which
 *  opens its route. */
export function RoutesIndex() {
  const { t } = useTranslation();
  const id = useAgencyId();
  if (id == null) return null;
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.routes")} />
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* key={id}: an agency switch starts the table at its default length. */}
          <RouteTableScreen key={id} />
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
