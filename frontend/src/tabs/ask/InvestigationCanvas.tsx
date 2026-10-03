import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { MessageList } from "./MessageList";
import { investigationSteps } from "./investigationSteps";
import type { NextStepAction } from "./nextStepChips";
import { ResultExports } from "./ResultExports";
import { stopEvidence, type StopFocus } from "./stopEvidence";
import { StopEvidenceChart } from "./StopEvidenceChart";
import { StopPatternResult } from "./StopPatternResult";
import { Tooltip } from "../../components/Tooltip";
import "./investigation.css";

export function InvestigationCanvas({ agencyId, messages, formatRoute, onStepChange, onChip, children }: {
  agencyId: number;
  messages: ConvMessage[];
  formatRoute: (code: string | null | undefined) => string;
  onStepChange?: () => void;
  /** Forwarded to every rendered evidence card's next-step chips — see
   *  RichResult/nextStepChips. The owning AskTab implements the actual
   *  filter/map/compare-previous behavior; this component only threads it
   *  through to whichever message list is on screen. */
  onChip?: (action: NextStepAction) => void;
  children?: ReactNode | ((context: { messages: ConvMessage[]; focus: StopFocus | null }) => ReactNode);
}) {
  const { t } = useTranslation();
  const steps = investigationSteps(messages);
  const latest = steps.at(-1);
  const [selection, setSelection] = useState<{ id: number; latestId: number } | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [recordedOpen, setRecordedOpen] = useState(false);
  const [focusEdit, setFocusEdit] = useState<{ stepId: number; focus: StopFocus | null } | null>(null);
  function selectStep(next: { id: number; latestId: number } | null) {
    setSelection(next);
    onStepChange?.();
  }
  const selected = selection?.latestId === latest?.id
    ? steps.find((step) => step.id === selection?.id) ?? latest
    : latest;
  if (!selected || !latest) return null;
  const isLatest = selected.id === latest.id;
  const focus = focusEdit?.stepId === selected.id ? focusEdit.focus : null;
  const answers = selected.messages.filter((message) => message.role === "assistant");
  const sourceId = answers.find((message) => typeof message.args?.context_message_id === "number")?.args?.context_message_id;
  const source = messages.find((message) => message.message_id === sourceId);
  const visibleAnswers = source && stopEvidence(source) ? [source, ...answers] : answers;

  return (
    <section className="investigation" aria-label={t("ask.workspace.title")}>
      <header className="investigation-heading">
        <Tooltip label={selected.question || t("ask.workspace.retained_result")}>
          <h2
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- heading, not a control; keyboard-focusable only so the tooltip revealing the untruncated question is reachable
            tabIndex={0}
          >
            {selected.question.split("\n")[0] || t("ask.workspace.retained_result")}
          </h2>
        </Tooltip>
      </header>
      <details className="investigation-history">
        <summary>{t("ask.workspace.steps")} · {t("ask.workspace.step_count", { count: steps.length })}</summary>
      <nav className="investigation-steps" aria-label={t("ask.workspace.steps")}>
        {steps.map((step, index) => (
          <Tooltip key={step.id} label={step.question || t("ask.workspace.retained_result")}>
            <button
              type="button"
              aria-current={step.id === selected.id ? "step" : undefined}
              onClick={() => selectStep({ id: step.id, latestId: latest.id })}
            >
              {index + 1}. {step.question.split("\n")[0] || t("ask.workspace.retained_result")}
            </button>
          </Tooltip>
        ))}
      </nav>
      </details>
      {!isLatest && (
        <div className="investigation-history-notice">
          <span>{t("ask.workspace.historical")}</span>
          <button type="button" onClick={() => selectStep(null)}>{t("ask.workspace.return_latest")}</button>
        </div>
      )}
      <p className="investigation-caption">{t("ask.workspace.saved_result_notice")}</p>
      {visibleAnswers.map((message) => {
        const points = stopEvidence(message);
        if (points && message.tool === "route_stop_patterns") return <StopPatternResult
          key={`${selected.id}:${message.message_id}`} messageId={message.message_id} points={points}
          message={message} onFocus={(next) => setFocusEdit({ stepId: selected.id, focus: next })} />;
        return points ? <StopEvidenceChart key={`${selected.id}:${message.message_id}`} messageId={message.message_id} points={points}
          message={message} onFocus={(next) => setFocusEdit({ stepId: selected.id, focus: next })} />
          : <MessageList key={message.message_id} messages={[message]} formatRoute={formatRoute} t={t} onChip={onChip} />;
      })}
      <details
        className="investigation-log"
        open={recordedOpen}
        onToggle={(event) => setRecordedOpen(event.currentTarget.open)}
      >
        <summary>{t("ask.evidence.recorded")}</summary>
        {recordedOpen && <MessageList messages={selected.messages.filter((message) => message.role !== "user")} formatRoute={formatRoute} t={t} onChip={onChip} />}
      </details>
      <ResultExports key={selected.id} agencyId={agencyId} step={selected} />
      {isLatest && (typeof children === "function" ? children({ messages, focus }) : children)}
      <details
        className="investigation-log"
        open={logOpen}
        onToggle={(event) => setLogOpen(event.currentTarget.open)}
      >
        <summary>{t("ask.workspace.full_log")}</summary>
        {logOpen && <MessageList messages={messages} formatRoute={formatRoute} t={t} onChip={onChip} />}
      </details>
    </section>
  );
}
