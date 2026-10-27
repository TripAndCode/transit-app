import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { StopChart } from "./StopChart";
import type { RouteShapeStop } from "../../api/types";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const stop: RouteShapeStop = {
  stop_id: "A",
  stop_name: "Stop A",
  stop_sequence: 1,
  avg_min: 2,
  samples: 5,
  lon: 140,
  lat: 40,
};

describe("StopChart stop labels", () => {
  it("leaves out the every-Nth label that would crowd the last stop's", () => {
    const stops = Array.from({ length: 22 }, (_, i) => ({ ...stop, stop_id: `S${i}`, stop_sequence: i + 1, stop_name: `Stop ${i}` }));
    const { container } = render(<StopChart stops={stops} previous={[]} selected={1} onSelect={() => {}} />);
    const labels = [...container.querySelectorAll("text")].map((t) => t.textContent).filter((l) => l?.startsWith("Stop"));
    expect(labels).toEqual(["Stop 0", "Stop 4", "Stop 8", "Stop 12", "Stop 16", "Stop 21"]);
  });
});

/** A measurer at 1em per character, the width of a CJK glyph. */
class FakeOffscreenCanvas {
  getContext() {
    return {
      font: "",
      measureText(this: { font: string }, text: string) {
        const size = Number(/(\d+(?:\.\d+)?)px/.exec(this.font)?.[1] ?? 10);
        return { width: [...text].length * size };
      },
    };
  }
}

describe("StopChart stop names", () => {
  const named = (names: string[]) =>
    names.map((stop_name, i) => ({ ...stop, stop_id: `S${i}`, stop_sequence: i + 1, stop_name }));
  const labels = (container: HTMLElement) =>
    [...container.querySelectorAll("text")].filter((t) => t.getAttribute("text-anchor") === "middle");

  it("shows a name longer than seven characters whole when it has the room", () => {
    vi.stubGlobal("OffscreenCanvas", FakeOffscreenCanvas);
    const names = ["港南", "市役所前", "県立中央病院前", "本町", "北部医療センター", "北町"];
    const { container } = render(<StopChart stops={named(names)} previous={[]} selected={1} onSelect={() => {}} />);
    expect(labels(container).map((t) => t.textContent)).toEqual(names);
  });

  it("ends a name that would overflow into its neighbour's with an ellipsis, and keeps it inside its room", () => {
    vi.stubGlobal("OffscreenCanvas", FakeOffscreenCanvas);
    const long = "デイサービスセンターふぁみりい尾道トッツ本社前";
    const { container } = render(
      <StopChart stops={named(["港南", long, "中央駅", "本町", "東台", long])} previous={[]} selected={1} onSelect={() => {}} />,
    );
    const shown = labels(container);
    for (const i of [1, 5]) {
      const text = shown[i].textContent!;
      expect(text.endsWith("…")).toBe(true);
      expect(long.startsWith(text.slice(0, -1))).toBe(true);
    }
    // Six stops 140 units apart: no label wider than the space between two.
    const xs = shown.map((t) => Number(t.getAttribute("x")));
    const widths = shown.map((t) => [...t.textContent!].length * 14);
    for (let i = 1; i < shown.length; i++) expect(xs[i] - widths[i] / 2).toBeGreaterThanOrEqual(xs[i - 1] + widths[i - 1] / 2);
    // The last stop sits 28 units from the right edge; its name still fits inside the chart.
    expect(xs[5] + widths[5] / 2).toBeLessThanOrEqual(780);
    expect(xs[0] - widths[0] / 2).toBeGreaterThanOrEqual(0);
  });

  it("shows every name whole where nothing can measure text", () => {
    vi.stubGlobal("OffscreenCanvas", undefined);
    const long = "デイサービスセンターふぁみりい尾道トッツ本社前";
    const { container } = render(<StopChart stops={named(["港南", long])} previous={[]} selected={1} onSelect={() => {}} />);
    expect(labels(container).map((t) => t.textContent)).toEqual(["港南", long]);
  });
});

describe("StopChart drawn at a given width", () => {
  const six = Array.from({ length: 6 }, (_, i) => ({ ...stop, stop_id: `S${i}`, stop_sequence: i + 1, stop_name: `Stop ${i}` }));

  it("draws one viewBox unit per pixel, so its labels keep their size in a narrow column", () => {
    const { container } = render(<StopChart stops={six} previous={[]} selected={1} onSelect={() => {}} width={316} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 316 335");
    expect(svg.style.width).toBe("316px");
    expect(svg.style.height).toBe("335px");
    // The plot runs from the value labels to the same right margin as at full width.
    const cxs = [...container.querySelectorAll("circle")].map((c) => Number(c.getAttribute("cx")));
    expect(cxs[0]).toBe(52);
    expect(cxs.at(-1)).toBe(316 - 28);
    for (const line of container.querySelectorAll("line")) expect(line.getAttribute("x2")).toBe(String(316 - 28));
  });

  it("names fewer stops where the stops sit closer together", () => {
    const { container } = render(<StopChart stops={six} previous={[]} selected={1} onSelect={() => {}} width={316} />);
    const names = [...container.querySelectorAll("text")].map((t) => t.textContent).filter((l) => l?.startsWith("Stop"));
    expect(names).toEqual(["Stop 0", "Stop 2", "Stop 5"]);
  });

  it("keeps the full-width drawing, and every name, when no width is given", () => {
    const { container } = render(<StopChart stops={six} previous={[]} selected={1} onSelect={() => {}} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 780 335");
    expect(svg.getAttribute("style")).toBeNull();
    const names = [...container.querySelectorAll("text")].map((t) => t.textContent).filter((l) => l?.startsWith("Stop"));
    expect(names).toHaveLength(6);
  });
});

describe("StopChart focus treatment", () => {
  it("marks each stop's role=button circle with the SVG-safe focus class, since outline is unreliable on SVG shapes", () => {
    render(
      <StopChart stops={[stop]} previous={[]} selected={1} onSelect={() => {}} />,
    );
    const target = screen.getByRole("button", { name: /Stop A/ });
    expect(target.tagName.toLowerCase()).toBe("circle");
    expect(target).toHaveClass("svg-focus-ring");
  });
});
