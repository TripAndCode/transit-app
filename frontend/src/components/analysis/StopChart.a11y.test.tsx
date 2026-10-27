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
