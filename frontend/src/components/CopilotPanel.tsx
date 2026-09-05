import { useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCopilotInsight } from "../api/copilot";
import { useRangeContext } from "../api/rangeContext";
import { useOverviewSummary } from "../api/hooks";

export function CopilotPanel() {
  const { t } = useTranslation();
  const overviewMatch = useMatch("/agencies/:agencyId/overview");
  const askMatch = useMatch("/agencies/:agencyId/ask");
  const agencyId = overviewMatch
    ? Number(overviewMatch.params.agencyId)
    : askMatch
      ? Number(askMatch.params.agencyId)
      : null;
  const [filters] = useRangeContext();
  const overviewQuery = useOverviewSummary(overviewMatch ? agencyId : null, filters);
  // Every hook below must run on every render regardless of which tab is
  // active — react-hooks/rules-of-hooks forbids branching before a hook
  // call, and this panel persists across tab navigation (it's mounted
  // outside <Outlet />) rather than remounting, so an early return above
  // this point would change the hook count between renders of the same
  // instance.
  const tab = overviewMatch ? "overview" : null;
  const { insight, loading, error } = useCopilotInsight(agencyId, tab, filters, overviewQuery.data ?? null);

  if (askMatch) {
    return (
      <aside className="copilot-panel" aria-label={t("copilot.title")}>
        <p>{t("copilot.ask_step_back")}</p>
      </aside>
    );
  }

  return (
    <aside className="copilot-panel" aria-label={t("copilot.title")}>
      <h2>{t("copilot.title")}</h2>
      {loading && <p>{t("copilot.loading")}</p>}
      {error != null && <p>{t("copilot.error")}</p>}
      {insight && (
        <div>
          <p>{insight.text}</p>
          <p className="copilot-cite">{insight.cite}</p>
          {insight.lowConfidence && <p className="copilot-low-confidence">{t("copilot.low_confidence")}</p>}
        </div>
      )}
    </aside>
  );
}
