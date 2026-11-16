import type { CardConditions, ConvMessage } from "../../api/types";
import { buildCsv, triggerBlobDownload, type CsvColumn } from "../../components/analysis/csv";
import type { InvestigationStep } from "./investigationSteps";

/** The filters a message's dispatch ran under, when its record keeps the
 *  period as well; a message answered before it did has only part of them. */
function recordedFilterCtx(message: ConvMessage): CardConditions | null {
  const c = message.conditions;
  return c && typeof c.from_date === "string" && typeof c.to_date === "string" ? c : null;
}

const FRESHNESS_NOTE = "Message timestamps are not source-data freshness.";

export function resultSnapshot(agencyId: number, step: InvestigationStep, exportedAt: string) {
  const filters = step.messages.map(recordedFilterCtx).filter((c) => c != null).at(-1) ?? null;
  return JSON.stringify({
    schema: "transit.ask-result.v1",
    agency_id: agencyId,
    exported_at: exportedAt,
    data_as_of: null,
    historical_filter_ctx: filters,
    provenance_note:
      filters != null
        ? FRESHNESS_NOTE
        : `${FRESHNESS_NOTE} Full historical filters were not recorded; only recorded tool arguments are included.`,
    question: step.question,
    messages: step.messages,
  }, null, 2);
}

/** A table answer as CSV rows: a key/value preamble recording what produced
 *  it, ending with `credit` (the agency's data-source credit) when one
 *  applies, then a blank row and the table. */
export function resultTableCsv(agencyId: number, message: ConvMessage, credit: string | null): unknown[][] | null {
  const result = message.result;
  if (message.role !== "assistant" || result?.kind !== "table" || !result.columns || !result.rows?.every(Array.isArray)) return null;
  const rows = result.rows as unknown[][];
  const columns: CsvColumn<unknown[]>[] = result.columns.map((header, i) => ({ header, value: (row) => row[i] }));
  const filters = recordedFilterCtx(message);
  return [
    ["agency_id", agencyId],
    ["conversation_id", message.conversation_id],
    ["message_id", message.message_id],
    ["message_created_at", message.created_at],
    ["data_as_of", "unknown"],
    ["historical_filter_ctx", filters ? JSON.stringify(filters) : "not_recorded"],
    ["tool", message.tool],
    ["recorded_args", JSON.stringify(message.args)],
    ["summary", result.summary],
    ...(credit == null ? [] : [["data_source", credit]]),
    [], ...buildCsv(rows, columns),
  ];
}

export function downloadSnapshot(agencyId: number, step: InvestigationStep) {
  const json = resultSnapshot(agencyId, step, new Date().toISOString());
  triggerBlobDownload(
    new Blob([json], { type: "application/json;charset=utf-8" }),
    `ask-${agencyId}-${step.id}.json`,
  );
}
