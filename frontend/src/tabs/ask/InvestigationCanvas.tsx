import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { MessageList } from "./MessageList";
import { investigationSteps } from "./investigationSteps";
import { ResultExports } from "./ResultExports";
import "./investigation.css";

export function InvestigationCanvas({ agencyId, messages, formatRoute, children }: {
  agencyId: number;
  messages: ConvMessage[];
  formatRoute: (code: string | null | undefined) => string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const steps = investigationSteps(messages);
  const latest = steps.at(-1);
  const [selection, setSelection] = useState<{ id: number; latestId: number } | null>(null);
  const selected = selection?.latestId === latest?.id
    ? steps.find((step) => step.id === selection?.id) ?? latest
    : latest;
  if (!selected || !latest) return null;
  const isLatest = selected.id === latest.id;

  return (
    <section className="investigation" aria-label={t("ask.workspace.title")}>
      <header className="investigation-heading">
        <h2>{selected.question || t("ask.workspace.retained_result")}</h2>
      </header>
      <details className="investigation-history">
        <summary>{t("ask.workspace.steps")} · {t("ask.workspace.step_count", { count: steps.length })}</summary>
      <nav className="investigation-steps" aria-label={t("ask.workspace.steps")}>
        {steps.map((step, index) => (
          <button
            key={step.id}
            type="button"
            aria-current={step.id === selected.id ? "step" : undefined}
            onClick={() => setSelection({ id: step.id, latestId: latest.id })}
            title={step.question || t("ask.workspace.retained_result")}
          >
            {index + 1}. {step.question || t("ask.workspace.retained_result")}
          </button>
        ))}
      </nav>
      </details>
      {!isLatest && (
        <div className="investigation-history-notice">
          <span>{t("ask.workspace.historical")}</span>
          <button type="button" onClick={() => setSelection(null)}>{t("ask.workspace.return_latest")}</button>
        </div>
      )}
      <p className="investigation-caption">{t("ask.workspace.saved_result_notice")}</p>
      <MessageList messages={selected.messages.filter((message) => message.role !== "user")} formatRoute={formatRoute} t={t} />
      <ResultExports key={selected.id} agencyId={agencyId} step={selected} />
      {isLatest && children}
      <details className="investigation-log">
        <summary>{t("ask.workspace.full_log")}</summary>
        <MessageList messages={messages} formatRoute={formatRoute} t={t} />
      </details>
    </section>
  );
}
