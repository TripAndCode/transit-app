// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { ConvMessage } from "../../api/types";
import { csvText } from "../../components/analysis/csv";
import { resultSnapshot, resultTableCsv } from "./resultSnapshot";

const message: ConvMessage = {
  message_id: 2, conversation_id: "thread", role: "assistant", chip_id: null,
  tool: "ranking", args: { routes: ["C10"] }, signature_hash: null,
  result: { kind: "table", summary: "Stored result", columns: ["route", "delay"],
    rows: [["=formula", -3], ...Array.from({ length: 60 }, () => ["C10", 5])], series: null, pairs: null },
  rendered_summary: "Stored answer", created_at: "2026-09-01T00:00:00Z",
};

describe("result snapshot", () => {
  it("preserves recorded results and arguments, not fabricated historical filters or freshness", () => {
    const json = resultSnapshot(9, { id: 1, question: "Which route?", messages: [message] }, "2026-09-14T00:00:00Z");
    const snapshot = JSON.parse(json);
    expect(snapshot.agency_id).toBe(9);
    expect(snapshot.messages).toEqual([message]);
    expect(snapshot.data_as_of).toBeNull();
    expect(snapshot.historical_filter_ctx).toBeNull();
    expect(snapshot.exported_at).not.toBe(message.created_at);
    expect(snapshot.schema).toBe("transit.ask-result.v1");
  });
  it("exports all table rows, including beyond the UI preview limit, formula safely", () => {
    const rows = resultTableCsv(9, message, null)!;
    expect(rows.slice(-61)).toEqual(message.result!.rows);
    expect(rows).toContainEqual(["message_created_at", message.created_at]);
    expect(rows).toContainEqual(["historical_filter_ctx", "not_recorded"]);
    expect(csvText(rows)).toContain('"\'=formula","-3"');
  });
  it("does not offer table CSV for text, charts, or malformed rows", () => {
    expect(resultTableCsv(9, { ...message, result: null }, null)).toBeNull();
    expect(resultTableCsv(9, { ...message, role: "user" }, null)).toBeNull();
    expect(resultTableCsv(9, { ...message, result: { ...message.result!, kind: "series" } }, null)).toBeNull();
    expect(resultTableCsv(9, { ...message, result: { ...message.result!, rows: [{}] } }, null)).toBeNull();
  });
  it("ends the preamble with the agency's data-source credit, ahead of the table", () => {
    const rows = resultTableCsv(9, message, "Credit line")!;
    const header = rows.findIndex((row) => row[0] === "route" && row[1] === "delay");
    expect(rows.slice(header - 3, header)).toEqual([["summary", "Stored result"], ["data_source", "Credit line"], []]);
  });
  it("leaves the credit row out when no source covers the agency", () => {
    expect(resultTableCsv(9, message, null)!.some((row) => row[0] === "data_source")).toBe(false);
  });
});
