import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { ConcentrationBar } from "./ConcentrationBar";
import type { OverviewConcentration } from "../api/types";

const concentration: OverviewConcentration = {
  top_routes: [
    { route_code: "A", route_short_name: null, share_pct: 30 },
    { route_code: "B", route_short_name: null, share_pct: 20 },
  ],
  rest_share_pct: 50,
  rest_route_count: 4,
};

describe("ConcentrationBar", () => {
  it("draws the largest share at full width and the rest in proportion to it", () => {
    const { container } = render(
      <ConcentrationBar
        concentration={{
          top_routes: [
            { route_code: "A", route_short_name: null, share_pct: 3.9 },
            { route_code: "B", route_short_name: null, share_pct: 1.3 },
          ],
          rest_share_pct: 94.8,
          rest_route_count: 40,
        }}
      />,
    );
    const fills = Array.from(container.querySelectorAll<HTMLElement>(".ov-pareto-fill")).map((f) => parseFloat(f.style.width));
    expect(fills[0]).toBe(100);
    expect(fills[1]).toBeCloseTo(33.33, 1);
  });

  it("hands each fill its rank opacity as a custom property, so the stylesheet's hover rule can still change it", () => {
    const { container } = renderWithProviders(<ConcentrationBar concentration={concentration} />);
    const fills = [...container.querySelectorAll<HTMLElement>(".ov-pareto-fill")];
    expect(fills).toHaveLength(2);
    // An inline `opacity` outranks every class rule, which would leave the
    // hover affordance in overview.css with nothing to act on.
    expect(fills.map((f) => f.style.opacity)).toEqual(["", ""]);
    expect(fills.map((f) => f.style.getPropertyValue("--rank-opacity"))).toEqual(["1", "0.8"]);
  });
});
