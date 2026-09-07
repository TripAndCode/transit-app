import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { DefinitionMetaBlock } from "./DefinitionMetaBlock";
import type { DefinitionMeta } from "../api/types";

describe("DefinitionMetaBlock", () => {
  it("renders the legacy preset with an unbounded early tolerance", () => {
    const definition: DefinitionMeta = {
      preset: "legacy_60s",
      early_tolerance_sec: null,
      late_tolerance_sec: 60,
      exclusion_threshold_sec: 7200,
      measurement_point: "all_stops_all_observations",
      dedup_rule: "latest_observation_per_stop_event",
    };
    renderWithProviders(<DefinitionMetaBlock definition={definition} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("legacy_60s");
    expect(block).toHaveTextContent("unbounded");
    expect(block).toHaveTextContent("60s");
    expect(block).toHaveTextContent("7200");
  });

  it("renders exact custom tolerance values, not the legacy defaults", () => {
    // Non-default tolerances must render their own values, never the
    // legacy_60s ones.
    const definition: DefinitionMeta = {
      preset: "custom",
      early_tolerance_sec: 30,
      late_tolerance_sec: 120,
      exclusion_threshold_sec: 7200,
      measurement_point: "all_stops_all_observations",
      dedup_rule: "latest_observation_per_stop_event",
    };
    renderWithProviders(<DefinitionMetaBlock definition={definition} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("custom");
    expect(block).toHaveTextContent("30s");
    expect(block).toHaveTextContent("120s");
    expect(block).not.toHaveTextContent("legacy_60s");
  });

  it("omits the tolerance line entirely for report types with no tolerance concept", () => {
    const definition: DefinitionMeta = {
      preset: null,
      early_tolerance_sec: null,
      late_tolerance_sec: null,
      exclusion_threshold_sec: 7200,
      measurement_point: "all_stops_all_observations",
      dedup_rule: "latest_observation_per_stop_event",
    };
    renderWithProviders(<DefinitionMetaBlock definition={definition} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).not.toHaveTextContent("On-time definition");
    expect(block).toHaveTextContent("all stops, all observations");
    expect(block).toHaveTextContent("7200");
  });

  it("renders measurement_point/dedup_rule text derived from the API's own fields, not a fixed string", () => {
    // An unrecognized identifier must render as-is (no matching i18n key),
    // proving the component actually reads definition.measurement_point/
    // definition.dedup_rule instead of a static translation key that would
    // show the same text regardless of what the API sent.
    const definition: DefinitionMeta = {
      preset: null,
      early_tolerance_sec: null,
      late_tolerance_sec: null,
      exclusion_threshold_sec: 7200,
      measurement_point: "some_other_measurement_point",
      dedup_rule: "some_other_dedup_rule",
    };
    renderWithProviders(<DefinitionMetaBlock definition={definition} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("some_other_measurement_point");
    expect(block).toHaveTextContent("some_other_dedup_rule");
    expect(block).not.toHaveTextContent("all stops, all observations");
    expect(block).not.toHaveTextContent("latest observation per stop event wins");
  });
});
