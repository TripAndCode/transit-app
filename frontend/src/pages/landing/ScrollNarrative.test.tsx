import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { ScrollNarrative } from "./ScrollNarrative";
import { PREVIEW_DAILY_TREND, sampleAskEvidence, sampleRouteStops } from "./previewData";
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
  afterEach(() => vi.restoreAllMocks());

  it("mounts the real StopChart, DailyChart, and StopEvidenceChart, each with fixture data", () => {
    renderNarrative();
    // StopChart: role="group" SVG with the design-namespace label.
    expect(screen.getByRole("group", { name: "Delay by stop" })).toBeTruthy();
    // DailyChart: role="img" SVG.
    expect(screen.getByRole("img", { name: "Daily average delay chart" })).toBeTruthy();
    // StopEvidenceChart: a labelled section, the same evidence view Ask renders.
    expect(screen.getByText("Highest average departure delays")).toBeTruthy();
  });

  it("shows each stop with the same figure in the stop chart and the Ask evidence", () => {
    renderNarrative();
    const chart = screen.getByRole("group", { name: "Delay by stop" });
    const onChart = new Map(
      within(chart)
        .getAllByRole("button")
        .map((b) => b.getAttribute("aria-label")!.match(/^(.+): ([\d.]+) min$/)!)
        .map(([, name, minutes]) => [name, Number(minutes)]),
    );
    const evidence = within(screen.getByRole("group", { name: "Select a bar to inspect its observations" })).getAllByRole("button");
    expect(evidence.length).toBeGreaterThan(0);
    for (const bar of evidence) {
      const [, name, minutes] = bar.getAttribute("aria-label")!.match(/^(.+), sequence \d+: ([\d.]+) minutes/)!;
      expect(onChart.get(name), name).toBe(Number(minutes));
    }
  });

  it("returns the sample route's worst stops for Ask, ranked, as the real hotspot answer does", () => {
    const evidence = sampleAskEvidence((key) => key);
    const { current } = sampleRouteStops((key) => key);
    expect(evidence.length).toBeLessThan(current.length);
    const worst = [...current].sort((a, b) => (b.avg_min ?? 0) - (a.avg_min ?? 0)).slice(0, evidence.length);
    expect(evidence.map((p) => [p.name, p.minutes])).toEqual(worst.map((s) => [s.stop_name, s.avg_min]));
  });

  it("averages, across the sample route, what its daily trend shows for the same two weeks", () => {
    const { current, previous } = sampleRouteStops((key) => key);
    const mean = (rows: { m: number; n: number }[]) => rows.reduce((s, r) => s + r.m * r.n, 0) / rows.reduce((s, r) => s + r.n, 0);
    const days = PREVIEW_DAILY_TREND.map((d) => ({ m: d.avg_min, n: d.samples }));
    expect(Math.abs(mean(current.map((s) => ({ m: s.avg_min!, n: s.samples }))) - mean(days.slice(7)))).toBeLessThan(0.1);
    expect(Math.abs(mean(previous.map((s) => ({ m: s.avg_min!, n: s.samples }))) - mean(days.slice(0, 7)))).toBeLessThan(0.1);
  });

  it("names the sample stops in the reader's language, the hero's names, in both stop charts", async () => {
    const { container } = renderNarrative();
    // The stop chart's labels and the evidence bars' names.
    const names = () => [...container.querySelectorAll("text[text-anchor='middle'], .stop-evidence-name")].map((n) => n.textContent);
    expect(names().filter((n) => n === "Honmachi")).toHaveLength(2);
    try {
      await i18n.changeLanguage("ja");
      await waitFor(() => expect(names().filter((n) => n === "本町")).toHaveLength(2));
      expect(names()).toContain("北町");
      expect(names().filter((n) => /[A-Za-z]/.test(n ?? ""))).toEqual([]);
      expect(screen.getAllByText("サンプルデータ · 架空の12系統")).toHaveLength(3);
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("fits the stop chart to a narrow column instead of scrolling it, and asks for no sideways scroll", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 316 } as DOMRect);
    renderNarrative();
    expect(screen.getByRole("group", { name: "Delay by stop" }).getAttribute("viewBox")).toBe("0 0 316 335");
    expect(screen.queryByText("Scroll sideways to see the whole chart.")).toBeNull();
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

  it("leaves a desktop column wide enough for the stop chart's floor", () => {
    // The card's inner width is the column, less its side padding and the
    // card's padding and border.
    const column = Number.parseFloat(decl(ruleBody(css, ".landing-narrative {"), "max-width") ?? "");
    const inner = column - 2 * 24 - 2 * 20 - 2 * 1;
    const floor = Number.parseFloat(decl(ruleBody(css, ".landing-narrative-section__figure .focus-chart {"), "min-width") ?? "");
    expect(inner).toBeGreaterThanOrEqual(floor);
  });

  it("keeps the stop chart readable before it is measured, at its full-width floor", () => {
    const minWidth = Number.parseFloat(decl(ruleBody(css, ".landing-narrative-section__figure .focus-chart {"), "min-width") ?? "");
    expect(minWidth).toBeGreaterThanOrEqual(700);
  });

  it("lets the evidence bars share a narrow figure rather than overflow it", () => {
    expect(decl(ruleBody(css, ".landing-narrative-section__figure .stop-evidence-column {"), "min-width")).toBe("0");
    expect(css).not.toMatch(/landing-narrative-section__hint/);
  });

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
