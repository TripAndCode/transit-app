import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BandGrid, Legend } from "./DowBandGrid";
import { BAND_ORDER, type ForecastOverviewGridCell } from "../../api/types";

function fullGrid(populate: { dow: number; band: string; v: number; n?: number }[] = []): ForecastOverviewGridCell[] {
  const set = new Map(populate.map((p) => [`${p.dow}-${p.band}`, p]));
  const grid: ForecastOverviewGridCell[] = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) {
      const p = set.get(`${dow}-${band}`);
      grid.push({
        dow,
        band,
        expected_avg_min: p ? p.v : null,
        samples: p ? (p.n ?? 200) : 0,
        low_confidence: p ? (p.n ?? 200) < 30 : false,
      });
    }
  }
  return grid;
}

describe("BandGrid", () => {
  it("renders all 35 cells", () => {
    render(
      <BandGrid
        grid={fullGrid([{ dow: 1, band: "midday", v: 6.8 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
        axisMin="min"
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    expect(screen.getAllByTestId("ov-band-cell")).toHaveLength(35);
  });

  it("dims low-confidence cells to 0.5 opacity", () => {
    render(
      <BandGrid
        grid={fullGrid([{ dow: 2, band: "evening", v: 9.0, n: 5 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
        axisMin="min"
        colorFor={() => "#abc"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    const cells = screen.getAllByTestId("ov-band-cell");
    const populated = cells.find((c) => (c as HTMLElement).style.background === "rgb(170, 187, 204)");
    expect(populated).toBeTruthy();
    expect((populated as HTMLElement).style.opacity).toBe("0.5");
  });
});

describe("Legend", () => {
  it("renders 6 gradient swatches with min/max/unit labels", () => {
    render(<Legend min={1.2} max={3.3} unit="min" colorFor={() => "#123"} />);
    expect(screen.getByText("1.2")).toBeTruthy();
    expect(screen.getByText("3.3")).toBeTruthy();
    expect(screen.getByText("min")).toBeTruthy();
  });
});
