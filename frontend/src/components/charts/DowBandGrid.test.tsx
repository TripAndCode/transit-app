import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { BandGrid, Legend } from "./DowBandGrid";
import { BAND_ORDER } from "../../api/timeBands";
import { type ForecastOverviewGridCell } from "../../api/types";
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
        source: "baseline" as const,
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
        grid={fullGrid([{ dow: 1, band: "forenoon", v: 6.8 }])}
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
    expect(screen.getAllByRole("gridcell")).toHaveLength(7 * BAND_ORDER.length);
    expect(screen.getByRole("gridcell", { name: /^Day1 Late morning 9–12h · 6\.8/ })).toBeInTheDocument();
    expect(screen.getByRole("gridcell", { name: "Day2 Late morning 9–12h · —" })).toBeInTheDocument();
  });

  it("shows the cell's tooltip and a highlight on keyboard focus, and clears both on blur", () => {
    const onTip = vi.fn();
    const onLeave = vi.fn();
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "forenoon", v: 6.8 }])}
        dayLabel={(d) => `Day${d}`}
        colorFor={() => "#000"}
        onTip={onTip}
        onLeave={onLeave}
      />,
    );
    const cell = screen.getByRole("gridcell", { name: /^Day1 Late morning 9–12h · 6\.8/ });
    expect(cell.style.outline).not.toContain("accent");
    act(() => cell.focus());
    expect(onTip).toHaveBeenCalledTimes(1);
    expect(onTip.mock.calls[0][0]).toEqual({ clientX: expect.any(Number), clientY: expect.any(Number) });
    expect(onTip.mock.calls[0][1]).toMatch(/^Day1 Late morning 9–12h · 6\.8/);
    expect(cell.style.outline).toContain("var(--accent)");
    act(() => cell.blur());
    expect(onLeave).toHaveBeenCalled();
    expect(cell.style.outline).not.toContain("accent");
  });

  it("declares the same row and column counts as the rows and headers it renders", () => {
    renderGrid();
    const grid = screen.getByRole("grid");
    expect(Number(grid.getAttribute("aria-rowcount"))).toBe(screen.getAllByRole("row").length);
    expect(Number(grid.getAttribute("aria-colcount"))).toBe(
      screen.getAllByRole("columnheader").length + screen.getAllByRole("rowheader").length / 7,
    );
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
    expect(document.activeElement).toBe(cells[1 + BAND_ORDER.length]);
  });
});

describe("BandGrid", () => {
  it("heads each column with the time-band filter's own band, named over its hours", () => {
    render(<BandGrid ariaLabel="Grid" grid={fullGrid([])} dayLabel={String} colorFor={() => "#000"} onTip={vi.fn()} onLeave={vi.fn()} />);
    const heads = screen.getAllByRole("columnheader");
    expect(heads.map((h) => h.getAttribute("aria-label"))).toEqual([
      "Late night 0–5h",
      "Morning 5–9h",
      "Late morning 9–12h",
      "Midday 12–14h",
      "Afternoon 14–17h",
      "Evening 17–20h",
      "Night 20–24h",
    ]);
    const evening = heads[5];
    expect(evening.querySelector(".band-grid__name")).toHaveTextContent("Evening");
    expect(evening.querySelector(".band-grid__hours")).toHaveTextContent("17–20h");
  });

  it("sizes its weekday column to the labels, so none breaks mid-word", () => {
    const { container } = render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([])}
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

  it("renders a cell for every weekday and band", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "forenoon", v: 6.8 }])}
        dayLabel={(d) => String(d)}
        colorFor={() => "#000"}
        onTip={vi.fn()}
        onLeave={vi.fn()}
      />,
    );
    expect(screen.getAllByTestId("ov-band-cell")).toHaveLength(7 * BAND_ORDER.length);
  });

  it("dims low-confidence cells to 0.5 opacity", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 2, band: "evening", v: 9.0, n: 5 }])}
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
      dayLabel: (d: number) => String(d),
      colorFor: () => "#000",
      onTip: vi.fn(),
      onLeave: vi.fn(),
    };
    const { rerender } = render(<BandGrid ariaLabel="Grid" grid={[]} {...props} />);
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    expect(screen.getAllByTestId("ov-band-cell")[0].classList.contains("chart-cell-enter--in")).toBe(false);

    rerender(<BandGrid ariaLabel="Grid" grid={fullGrid([{ dow: 1, band: "forenoon", v: 6.8 }])} {...props} />);
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    expect(screen.getAllByTestId("ov-band-cell")[0].classList.contains("chart-cell-enter--in")).toBe(true);
    vi.restoreAllMocks();
  });

  it("marks every cell with the staggered-fade entrance class", () => {
    render(
      <BandGrid
        ariaLabel="Grid"
        grid={fullGrid([{ dow: 1, band: "forenoon", v: 6.8 }])}
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
          { dow: 1, band: "forenoon", v: DELAY_THRESHOLDS.severe + 1 },
          { dow: 2, band: "forenoon", v: 1.0 },
        ])}
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
