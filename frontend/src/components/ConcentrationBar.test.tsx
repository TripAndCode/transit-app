import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { ConcentrationBar } from "./ConcentrationBar";
import type { OverviewConcentration } from "../api/types";

const concentration: OverviewConcentration = {
  top_routes: [
    { route_code: "A", route_short_name: null, share_pct: 30 },
    { route_code: "B", route_short_name: null, share_pct: 20 },
  ],
  rest_share_pct: 50,
};

describe("ConcentrationBar", () => {
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
