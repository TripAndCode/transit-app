import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { BandGrid, Legend } from "./DowBandGrid";
import { BAND_ORDER, type ForecastOverviewGridCell } from "../../api/types";
import { DELAY_THRESHOLDS } from "../../styles/tokens";

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

describe("BandGrid keyboard and screen-reader access", () => {
  function renderGrid() {
    return render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "midday", v: 6.8 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => `Day${d}`}
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
  }

  it("exposes each cell's value as its accessible name, not only a hover tooltip", () => {
    renderGrid();
    expect(screen.getByRole("grid")).toBeInTheDocument();
    expect(screen.getAllByRole("gridcell")).toHaveLength(35);
    expect(screen.getByRole("gridcell", { name: /^Day1 midday · 6\.8/ })).toBeInTheDocument();
    expect(screen.getByRole("gridcell", { name: "Day2 midday · —" })).toBeInTheDocument();
  });

  it("is one tab stop, with arrow keys moving between cells", () => {
    renderGrid();
    const cells = screen.getAllByRole("gridcell");
    expect(cells.filter((c) => c.getAttribute("tabindex") === "0")).toHaveLength(1);
    const first = cells[0];
    first.focus();
    fireEvent.keyDown(screen.getByRole("grid"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(cells[1]);
    fireEvent.keyDown(screen.getByRole("grid"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(cells[1 + 5]);
  });
});

describe("BandGrid", () => {
  it("sizes its weekday column to the labels, so none breaks mid-word", () => {
    const { container } = render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([])}
        bandLabel={(b) => b}
        dayLabel={() => "Wed"}
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    const grid = container.querySelector<HTMLElement>("[style*='grid-template-columns']")!;
    expect(grid.style.gridTemplateColumns.startsWith("auto ")).toBe(true);
    expect(screen.getAllByText("Wed")[0]).toHaveStyle({ whiteSpace: "nowrap" });
  });

  it("renders all 35 cells", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "midday", v: 6.8 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
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
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 2, band: "evening", v: 9.0, n: 5 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
        colorFor={() => "#abc"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    const cells = screen.getAllByTestId("ov-band-cell");
    const populated = cells.find((c) => (c as HTMLElement).style.background === "rgb(170, 187, 204)");
    expect(populated).toBeTruthy();
    // The target opacity a low-confidence cell fades in *to* is carried as a
    // CSS custom property (--cell-opacity), consumed by the .chart-cell-enter
    // stylesheet rule -- not a plain inline `opacity`, which would always
    // outrank that rule and leave nothing for the entrance fade to animate.
    expect((populated as HTMLElement).style.getPropertyValue("--cell-opacity")).toBe("0.5");
  });

  it("starts its staggered fade on the first frame after real data, not on an empty mount", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    const props = {
      bandLabel: (b: string) => b,
      dayLabel: (d: number) => String(d),
      colorFor: () => "#000",
      onTip: vi.fn(),
      onLeave: vi.fn(),
    };
    const { rerender } = render(<BandGrid ariaLabel="Grid" grid={[]} {...props} />);
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    expect(screen.getAllByTestId("ov-band-cell")[0].classList.contains("chart-cell-enter--in")).toBe(false);

    rerender(<BandGrid ariaLabel="Grid" grid={fullGrid([{ dow: 1, band: "midday", v: 6.8 }])} {...props} />);
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    expect(screen.getAllByTestId("ov-band-cell")[0].classList.contains("chart-cell-enter--in")).toBe(true);
    vi.restoreAllMocks();
  });

  it("marks every cell with the staggered-fade entrance class", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "midday", v: 6.8 }])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    for (const cell of screen.getAllByTestId("ov-band-cell")) {
      expect(cell.classList.contains("chart-cell-enter")).toBe(true);
    }
  });
});

describe("Legend", () => {
  it("renders 5 gradient swatches with min/max/unit labels", () => {
    const { container } = render(<Legend min={1.2} max={3.3} unit="min" colorFor={() => "#123"} />);
    expect(screen.getByText("1.2")).toBeTruthy();
    expect(screen.getByText("3.3")).toBeTruthy();
    expect(screen.getByText("min")).toBeTruthy();
    expect(container.querySelectorAll("span[style*='width: 14px']")).toHaveLength(5);
  });
});

describe("BandGrid severity outline", () => {
  it("outlines a cell at or above the severe threshold instead of recolouring it", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([
          { dow: 1, band: "midday", v: DELAY_THRESHOLDS.severe + 1 },
          { dow: 2, band: "midday", v: 1.0 },
        ])}
        bandLabel={(b) => b}
        dayLabel={(d) => String(d)}
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    const outlined = screen
      .getAllByTestId("ov-band-cell")
      .filter((c) => (c as HTMLElement).style.boxShadow.includes("--delay-severe"));
    expect(outlined).toHaveLength(1);
  });
});
