import { useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadCsv } from "../../components/analysis/csv";
import type { InvestigationStep } from "./investigationSteps";
import { downloadSnapshot, resultTableCsv } from "./resultSnapshot";

export function ResultExports({ agencyId, step }: { agencyId: number; step: InvestigationStep }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  if (!step.messages.some((message) => message.role === "assistant")) return null;
  const tables = step.messages.flatMap((message) => {
    const rows = resultTableCsv(agencyId, message);
    return rows ? [{ id: message.message_id, rows }] : [];
  });
  function save(action: () => void) {
    setFailed(false);
    try { action(); } catch { setFailed(true); }
  }
  return (
    <div className="investigation-exports">
      <div>
        <button type="button" onClick={() => save(() => downloadSnapshot(agencyId, step))}>
          {t("ask.workspace.export_snapshot")}
        </button>
        {tables.map((table, index) => (
          <button key={table.id} type="button" onClick={() => save(() => downloadCsv(`ask-${agencyId}-${table.id}`, table.rows))}>
            {t("ask.workspace.export_csv", { index: index + 1 })}
          </button>
        ))}
      </div>
      <p className="investigation-caption">{t("ask.workspace.export_notice")}</p>
      {failed && <p role="alert">{t("ask.workspace.export_failed")}</p>}
    </div>
  );
}
