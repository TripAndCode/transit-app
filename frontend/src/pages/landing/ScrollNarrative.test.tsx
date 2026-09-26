import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { ScrollNarrative } from "./ScrollNarrative";
import { ruleBody, decl } from "../../test/cssRules";

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

  it("gives every section the reveal wrapper, already revealed without IntersectionObserver support", () => {
    // Forced, not inherited: the shared setup installs an inert observer so
    // an unguarded component still mounts, which means absence is no longer
    // jsdom's ambient default. An inert observer would leave these sections
    // pre-reveal forever, which is the case this guards against.
    vi.stubGlobal("IntersectionObserver", undefined);
    expect(typeof IntersectionObserver).toBe("undefined");
    const { container } = renderNarrative();
    const sections = container.querySelectorAll(".landing-narrative-section");
    expect(sections.length).toBe(3);
    for (const section of sections) {
      expect(section.classList.contains("landing-reveal")).toBe(true);
      expect(section.classList.contains("landing-reveal--visible")).toBe(true);
    }
  });

  // The charts here illustrate; they do not drive anything. A brushable
  // DailyChart writes from/to into the URL on drag, which on an
  // unauthenticated marketing page changes nothing the visitor can see.
  it("renders no brush control on the demo charts", () => {
    renderNarrative();
    expect(screen.queryByRole("slider")).toBeNull();
  });
});

describe("landing reveal CSS", () => {
  const css = readFileSync(resolve(process.cwd(), "src/pages/landing/ScrollNarrative.css"), "utf8").replace(
    /\/\*[\s\S]*?\*\//g,
    "",
  );

  /** Body of the first rule whose selector text starts at `selector`. */

  it("never parks a narrative section at opacity: 0 -- the offset is the whole animation", () => {
    const pending = ruleBody(css, ".landing-reveal.landing-reveal--pending {");
    expect(decl(pending, "opacity")).toBeNull();
    expect(decl(pending, "transform")).toBe("translateY(28px)");
  });

  it("transitions the transform only, inside a motion-allowed block", () => {
    expect(css.match(/@media \(prefers-reduced-motion: no-preference\)/g)).toHaveLength(1);
    const visible = ruleBody(css, ".landing-reveal.landing-reveal--pending.landing-reveal--visible {");
    expect(decl(visible, "opacity")).toBeNull();
    expect(decl(visible, "transform")).toBe("translateY(0)");
    expect(decl(visible, "transition")).toBe("transform var(--dur-3) var(--ease-out)");
  });
});
