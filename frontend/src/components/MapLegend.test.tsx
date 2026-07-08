import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { MapLegend } from "./MapLegend";

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
    // Real i18n copy (en.json) uses en-dashes, not hyphens — verified against
    // frontend/src/i18n/locales/en.json:337-340 directly, not guessed.
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
