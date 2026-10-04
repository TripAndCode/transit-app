import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import "../i18n";
import { ConcentrationBar } from "./ConcentrationBar";

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
});
