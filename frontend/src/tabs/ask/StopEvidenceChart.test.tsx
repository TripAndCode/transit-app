import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { StopEvidenceChart } from "./StopEvidenceChart";
import type { StopEvidence } from "./stopEvidence";
import type { ConvMessage } from "../../api/types";

const t = i18n.getFixedT("en");

const points: StopEvidence[] = [
  { sequence: 1, name: "Central", minutes: 4.2, samples: 30 },
  { sequence: 2, name: "North", minutes: 5.1, samples: 25 },
];

function assistantMessage(overrides: Partial<ConvMessage> = {}): ConvMessage {
  return {
    message_id: 1,
    conversation_id: "thread",
    role: "assistant",
    chip_id: null,
    tool: "segment_hotspots",
    args: { route: "A05" },
    signature_hash: null,
    result: { kind: "table", summary: "x", rows: [], columns: [], series: null, pairs: null },
    rendered_summary: null,
    conditions: { dow: "weekend", time_band: "all", service: "all" },
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

describe("StopEvidenceChart provenance", () => {
  it("renders no provenance badge when no message is given (existing call sites unaffected)", () => {
    renderWithProviders(<StopEvidenceChart messageId={1} points={points} />);
    expect(screen.queryByText(t("ask.evidence.badge.sql"))).not.toBeInTheDocument();
  });

  it("renders the SQL badge and tool label when a dispatched message is given", () => {
    renderWithProviders(<StopEvidenceChart messageId={1} points={points} message={assistantMessage()} />);
    expect(screen.getByText(t("ask.evidence.badge.sql"))).toBeInTheDocument();
    expect(screen.getAllByText(t("ask.evidence.tool_label.segment_hotspots")).length).toBeGreaterThan(0);
  });

  it("extends the existing definition disclosure with route and conditions", () => {
    renderWithProviders(<StopEvidenceChart messageId={1} points={points} message={assistantMessage()} />);
    expect(screen.getByText("A05")).toBeInTheDocument();
    expect(screen.getByText(t("common.service_value.土日祝"))).toBeInTheDocument();
  });
});
