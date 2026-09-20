import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { ScrollNarrative } from "./ScrollNarrative";

void i18n.changeLanguage("en");

function renderNarrative() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <ScrollNarrative />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

describe("ScrollNarrative", () => {
  it("mounts the real StopChart, DailyChart, and StopEvidenceChart, each with fixture data", () => {
    renderNarrative();
    // StopChart: role="group" SVG with the design-namespace label.
    expect(screen.getByRole("group", { name: "Delay by stop" })).toBeTruthy();
    // DailyChart: role="img" SVG.
    expect(screen.getByRole("img", { name: "Daily average delay chart" })).toBeTruthy();
    // StopEvidenceChart: a labelled section, the same evidence view Ask renders.
    expect(screen.getByText("Highest average departure delays")).toBeTruthy();
  });

  it("renders one of the real fixture stop names inside the route section", () => {
    renderNarrative();
    expect(screen.getAllByText("Riverside Sta.").length).toBeGreaterThan(0);
  });

  it("gives every section the reveal wrapper classes, visible without JS/IntersectionObserver support", () => {
    const { container } = renderNarrative();
    const sections = container.querySelectorAll(".landing-narrative-section");
    expect(sections.length).toBe(3);
    for (const section of sections) {
      expect(section.classList.contains("landing-reveal")).toBe(true);
      expect(section.classList.contains("landing-reveal--pending")).toBe(true);
    }
  });
});
