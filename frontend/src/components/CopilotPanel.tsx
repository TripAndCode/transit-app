import { useState, type FormEvent } from "react";
import { useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { useCopilotEnabled, useCopilotInsight } from "../api/copilot";
import { apiPost } from "../api/client";
import { ErrorBanner } from "./ErrorBanner";
import { useScope } from "../api/scope";
import { useIsLlmApproved, useOverviewSummary } from "../api/hooks";
import type { AskResponse } from "../api/types";
import "./CopilotPanel.css";

/** The route whose data this panel summarizes. It is Pulse,
 *  not the realtime Live map: the payload comes from
 *  `useOverviewSummary` and the follow-up carries `panel_ctx.tab = "overview"`.
 *  Must stay outside `FOCUSED_TAB_SEGMENTS` — App renders this panel only when
 *  the route is unfocused, so a focused route here means it can never appear.
 */
export const COPILOT_INSIGHT_ROUTE = "/agencies/:agencyId/pulse";

export function CopilotPanel() {
  const { t } = useTranslation();
  const overviewMatch = useMatch(COPILOT_INSIGHT_ROUTE);
  const agencyId = overviewMatch ? Number(overviewMatch.params.agencyId) : null;
  const [filters] = useScope();
  // Anything but an explicit true is treated as off, so an unresolved or
  // failed flag check never reaches the insight POST.
  const enabled = useCopilotEnabled(agencyId).data?.enabled === true;
  const llmApproved = useIsLlmApproved();
  // Deliberately NOT gated on `enabled`: this is a free aggregate read that
  // OverviewTab already issues under the same query key, and the insight
  // POST is withheld by `tab` below. Gating it here would only stall the
  // insight behind the flag round trip on the enabled path.
  const overviewQuery = useOverviewSummary(agencyId, filters);
  // Every hook below must run on every render regardless of which tab is
  // active — react-hooks/rules-of-hooks forbids branching before a hook
  // call, and this panel persists across tab navigation (it's mounted
  // outside <Outlet />) rather than remounting, so an early return above
  // this point would change the hook count between renders of the same
  // instance.

  const tab = overviewMatch && enabled ? "overview" : null;
  const { insight, loading, error } = useCopilotInsight(agencyId, tab, overviewQuery.data ?? null);

  // The kill switch removes the panel outright rather than showing an empty
  // shell — a disabled feature should be invisible, not broken-looking.
  if (!enabled) return null;

  // Every other route (Live, the other destinations, Reports, Account,
  // Admin, root redirect, ...) has nothing for this panel to show — it only
  // ever has content on Pulse (the proactive insight). Placed after every
  // hook call above so the hook count stays identical across renders of this
  // always-mounted instance.
  if (!overviewMatch) return null;

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
      {/* The insight is drawn from the Pulse summary; with none to draw from
          (an agency with no data yet) the panel says so rather than standing
          empty under its heading. */}
      {!overviewQuery.isPending && overviewQuery.data == null && !insight && (
        <p className="copilot-empty">{t("copilot.no_insight")}</p>
      )}
      {/* The insight needs no approval; the follow-up goes to /ask, whose
          free-text stage answers only an admin-approved caller. */}
      {agencyId != null && tab != null && llmApproved && (
        <FollowupForm key={agencyId} agencyId={agencyId} tab={tab} />
      )}
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
