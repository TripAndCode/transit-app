import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import { useTranslation } from "react-i18next";
import { renderWithProviders } from "../../test/renderWithProviders";
import { MessageList } from "./MessageList";
import type { ConvMessage, DefinitionMeta } from "../../api/types";

const STAMP: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: "2026-11-05T15:30:00Z",
  value_basis: "feed_prediction",
  timetable_versions: [{ agency_id: 1, name: "gtfs_static_20260401", fetched_on: "2026-04-01", days: 7, content_hash: "01234567" }],
  source_mix: null,
  feed_quality: null,
};

function card(extra: Partial<ConvMessage>): ConvMessage {
  return {
    message_id: 2,
    conversation_id: "c1",
    role: "assistant",
    chip_id: null,
    tool: "on_time",
    args: { k: 5 },
    signature_hash: null,
    result: {
      kind: "table",
      summary: "On-time rate, worst 5 routes",
      rows: [["R1", 91.2, 1.1, 300, false]],
      columns: ["route_code", "on_time_pct", "avg_min", "samples", "low_confidence"],
      series: null,
      pairs: null,
    },
    rendered_summary: "On-time rate",
    conditions: { dow: "all", time_band: "all", service: "all" },
    created_at: "2026-11-14T00:00:00Z",
    ...extra,
  };
}

function List({ messages }: { messages: ConvMessage[] }) {
  const { t } = useTranslation();
  return <MessageList messages={messages} formatRoute={(rc) => rc ?? ""} t={t} />;
}

describe("a saved card's method stamp", () => {
  it("shows the stamp the card was saved with, in the reports' own block", () => {
    renderWithProviders(<List messages={[card({ definition: STAMP })]} />);
    const block = screen.getByTestId("definition-meta");
    expect(within(block).getByText(/content hash 01234567/)).toBeInTheDocument();
    expect(block.closest(".ask-evidence-card")).not.toBeNull();
  });

  it.each([
    ["saved before cards carried one", {}],
    ["with none, as a card no report computes is saved", { definition: null }],
  ])("shows no stamp for a card %s", (_label, extra) => {
    renderWithProviders(<List messages={[card(extra)]} />);
    expect(screen.getByText("On-time rate, worst 5 routes")).toBeInTheDocument();
    expect(screen.queryByTestId("definition-meta")).toBeNull();
  });
});
