import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { MapLegend } from "./MapLegend";

// Pin the language explicitly rather than relying on jsdom's default
// navigator.language — the shared i18n singleton's fallbackLng is "ja".
// Restored after this file so the mutation doesn't outlive it if isolation
// is ever relaxed.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});
afterAll(async () => {
  await i18n.changeLanguage("ja");
});

function renderLegend(overrides = {}) {
  const props = {
    showSingleSampleStops: false,
    onShowSingleSampleStopsChange: vi.fn(),
    focusedSeverity: null,
    onFocusedSeverityChange: vi.fn(),
    bandCounts: { ok: 10, mild: 5, moderate: 2, severe: 0 },
    ...overrides,
  };
  render(
    <I18nextProvider i18n={i18n}>
      <MapLegend {...props} />
    </I18nextProvider>,
  );
  return props;
}

describe("MapLegend", () => {
  it("renders all 4 severity band labels", () => {
    renderLegend();
    // Real i18n copy (map.legend.band_lt_2/band_2_5/band_5_10/band_gt_10 in
    // en.json) uses en-dashes, not hyphens — verified directly, not guessed.
    expect(screen.getByText("< 2 min")).toBeTruthy();
    expect(screen.getByText("2–5 min")).toBeTruthy();
    expect(screen.getByText("5–10 min")).toBeTruthy();
    expect(screen.getByText("> 10 min")).toBeTruthy();
  });

  it("clicking a band with stops calls onFocusedSeverityChange with that band's key", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByText("< 2 min"));
    expect(props.onFocusedSeverityChange).toHaveBeenCalledWith("ok");
  });

  it("does not call onFocusedSeverityChange when clicking a band with zero stops", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByText("> 10 min"));
    expect(props.onFocusedSeverityChange).not.toHaveBeenCalled();
  });

  it("toggles the single-sample-stops checkbox", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(props.onShowSingleSampleStopsChange).toHaveBeenCalledWith(true);
  });
});
