import { useState, type FormEvent } from "react";
import { useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { useCopilotEnabled, useCopilotInsight } from "../api/copilot";
import { apiPost, isCopilotQuotaExceeded } from "../api/client";
import { ErrorBanner } from "./ErrorBanner";
import { useRangeContext } from "../api/rangeContext";
import { useOverviewSummary } from "../api/hooks";
import type { AskResponse } from "../api/types";
import "./CopilotPanel.css";

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
  // Anything but an explicit true is treated as off, so an unresolved or
  // failed flag check never reaches the billed insight POST.
  const enabled = useCopilotEnabled(agencyId).data?.enabled === true;
  const overviewQuery = useOverviewSummary(overviewMatch && enabled ? agencyId : null, filters);
  // Every hook below must run on every render regardless of which tab is
  // active — react-hooks/rules-of-hooks forbids branching before a hook
  // call, and this panel persists across tab navigation (it's mounted
  // outside <Outlet />) rather than remounting, so an early return above
  // this point would change the hook count between renders of the same
  // instance.
  const tab = overviewMatch && enabled ? "overview" : null;
  const { insight, loading, error } = useCopilotInsight(agencyId, tab, filters, overviewQuery.data ?? null);

  // The kill switch removes the panel outright rather than showing an empty
  // shell — a disabled feature should be invisible, not broken-looking.
  if (!enabled) return null;

  if (askMatch) {
    return (
      <aside className="copilot-panel" aria-label={t("copilot.title")}>
        <p>{t("copilot.ask_step_back")}</p>
      </aside>
    );
  }

  // Every other route (Map, Live, Analysis, Network, Forecast, Account,
  // Admin, root redirect, ...) has nothing for this panel to show — it only
  // ever has content on Overview (the proactive insight) or Ask (handled
  // above). Placed after every hook call above so the hook count stays
  // identical across renders of this always-mounted instance.
  if (!overviewMatch) return null;

  return (
    <aside className="copilot-panel" aria-label={t("copilot.title")}>
      <h2>{t("copilot.title")}</h2>
      {loading && <p>{t("copilot.loading")}</p>}
      {error != null &&
        (isCopilotQuotaExceeded(error) ? <ErrorBanner error={error} /> : <p>{t("copilot.error")}</p>)}
      {insight && (
        <div>
          <p>{insight.text}</p>
          <p className="copilot-cite">{insight.cite}</p>
          {insight.lowConfidence && <p className="copilot-low-confidence">{t("copilot.low_confidence")}</p>}
        </div>
      )}
      {agencyId != null && tab != null && <FollowupForm key={agencyId} agencyId={agencyId} tab={tab} />}
    </aside>
  );
}

// Keyed by agencyId at the call site above so switching agencies remounts
// this component from scratch — otherwise the question/answer/error state
// below would persist across an agency switch, since CopilotPanel itself is
// mounted once outside <Outlet /> and never remounts on its own.
function FollowupForm({ agencyId, tab }: { agencyId: number; tab: string }) {
  const { t } = useTranslation();
  const [question, setQuestion] = useState("");
  // Reuses the existing /ask pipeline unchanged (rules → embedding → RAG),
  // just with the panel's current tab passed as a grounding hint — no new
  // routing/dispatch logic, per the Copilot spec's "explicitly out of
  // scope" constraint.
  const followup = useMutation({
    mutationFn: (q: string) =>
      apiPost<AskResponse>(`/api/${agencyId}/ask`, { question: q, panel_ctx: { tab } }),
  });

  function submitFollowup(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = question.trim();
    if (!trimmed) return;
    followup.mutate(trimmed);
    setQuestion("");
  }

  return (
    <>
      <form onSubmit={submitFollowup}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t("copilot.followup_placeholder")}
        />
        <button type="submit" disabled={followup.isPending}>
          {t("copilot.followup_submit")}
        </button>
      </form>
      {followup.error != null && <ErrorBanner error={followup.error} />}
      {followup.data && <p>{followup.data.answer}</p>}
    </>
  );
}
