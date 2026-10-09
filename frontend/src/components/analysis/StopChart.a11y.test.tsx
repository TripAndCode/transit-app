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
