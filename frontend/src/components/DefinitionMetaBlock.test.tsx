import { afterEach, describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import i18n from "../i18n";
import { DefinitionMetaBlock } from "./DefinitionMetaBlock";
import type { DefinitionMeta } from "../api/types";

const LEGACY: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

describe("DefinitionMetaBlock", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("keeps the method behind one 'How this is calculated' disclosure", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    const summary = screen.getByText("How this is calculated");
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("says how the figures are made in plain words, without preset ids or raw seconds", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Average of every recorded departure at every stop.");
    expect(block).toHaveTextContent("When a departure is reported more than once, the latest report counts.");
    expect(block).toHaveTextContent("Readings more than 2 hours off are ignored as errors.");
    expect(block).toHaveTextContent("On time = no more than 1 min late.");
    expect(block).not.toHaveTextContent("legacy_60s");
    expect(block).not.toHaveTextContent("7200");
  });

  it("runs Japanese sentences together, without a half-width space between them", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta").querySelector("p")?.textContent).toContain("平均です。同じ発車");
  });

  it("states a custom tolerance, early and late", () => {
    renderWithProviders(
      <DefinitionMetaBlock definition={{ ...LEGACY, preset: "custom", early_tolerance_sec: 30, late_tolerance_sec: 120 }} />,
    );
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "On time = no more than 2 min late and no more than 30 s early.",
    );
  });

  it("says nothing about on time for a report with no tolerance", () => {
    renderWithProviders(
      <DefinitionMetaBlock definition={{ ...LEGACY, preset: null, late_tolerance_sec: null }} />,
    );
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("On time");
  });

  it("shows an identifier this build has no words for as the API sent it", () => {
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, measurement_point: "some_other_measurement_point", dedup_rule: "some_other_dedup_rule" }}
      />,
    );
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("some_other_measurement_point");
    expect(block).toHaveTextContent("some_other_dedup_rule");
  });
});
