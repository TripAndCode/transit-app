import type { ConvMessage } from "../../api/types";
import type { InvestigationStep } from "./investigationSteps";

export function resultSnapshot(agencyId: number, step: InvestigationStep, exportedAt: string) {
  return JSON.stringify({
    schema: "transit.ask-result.v1",
    agency_id: agencyId,
    exported_at: exportedAt,
    data_as_of: null,
    historical_filter_ctx: null,
    provenance_note: "Message timestamps are not source-data freshness. Full historical filters were not recorded; only recorded tool arguments are included.",
    question: step.question,
    messages: step.messages,
  }, null, 2);
}

export function resultTableCsv(agencyId: number, message: ConvMessage): unknown[][] | null {
  const result = message.result;
  if (message.role !== "assistant" || result?.kind !== "table" || !result.columns || !result.rows?.every(Array.isArray)) return null;
  return [
    ["agency_id", agencyId],
    ["conversation_id", message.conversation_id],
    ["message_id", message.message_id],
    ["message_created_at", message.created_at],
    ["data_as_of", "unknown"],
    ["historical_filter_ctx", "not_recorded"],
    ["tool", message.tool],
    ["recorded_args", JSON.stringify(message.args)],
    ["summary", result.summary],
    [], result.columns, ...result.rows as unknown[][],
  ];
}

export function downloadSnapshot(agencyId: number, step: InvestigationStep) {
  const json = resultSnapshot(agencyId, step, new Date().toISOString());
  const url = URL.createObjectURL(new Blob([json], { type: "application/json;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `ask-${agencyId}-${step.id}.json`;
  document.body.append(anchor);
  try { anchor.click(); }
  finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
