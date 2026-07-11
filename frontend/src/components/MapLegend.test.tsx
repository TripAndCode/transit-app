import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { MapLegend } from "./MapLegend";

// Pin the language explicitly rather than relying on jsdom's default
// navigator.language — the shared i18n singleton's fallbackLng is "ja".
// Snapshot-and-restore (not a hardcoded "ja") so this doesn't outlive the
// file with the wrong value if isolation is ever relaxed or the fallback
// language changes later.
let prevLanguage: string;
beforeAll(async () => {
  prevLanguage = i18n.language;
  await i18n.changeLanguage("en");
});
afterAll(async () => {
  await i18n.changeLanguage(prevLanguage);
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
    // Real i18n copy (map.legend.band_lt_1_5/band_1_5_3/band_3_5/band_gt_5 in
    // en.json) uses en-dashes, not hyphens — verified directly, not guessed.
    expect(screen.getByText("< 1.5 min")).toBeTruthy();
    expect(screen.getByText("1.5–3 min")).toBeTruthy();
    expect(screen.getByText("3–5 min")).toBeTruthy();
    expect(screen.getByText("> 5 min")).toBeTruthy();
  });

  it("clicking a band with stops calls onFocusedSeverityChange with that band's key", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByText("< 1.5 min"));
    expect(props.onFocusedSeverityChange).toHaveBeenCalledWith("ok");
  });

  it("does not call onFocusedSeverityChange when clicking a band with zero stops", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByText("> 5 min"));
    expect(props.onFocusedSeverityChange).not.toHaveBeenCalled();
  });

  it("toggles the single-sample-stops checkbox", () => {
    const props = renderLegend();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(props.onShowSingleSampleStopsChange).toHaveBeenCalledWith(true);
  });
});
