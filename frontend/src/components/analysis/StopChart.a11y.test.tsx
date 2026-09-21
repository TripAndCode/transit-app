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
