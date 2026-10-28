import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { HourlyHeatmap, type HourlyCell } from "./HourlyHeatmap";
import { DELAY_THRESHOLDS, delayRampVar } from "../../styles/tokens";
import i18n from "../../i18n";

function renderHeatmap(cells: HourlyCell[]) {
  return renderWithProviders(
    <MemoryRouter>
      <HourlyHeatmap cells={cells} />
    </MemoryRouter>,
  );
}

// A single non-empty cell is enough to skip the empty-state early return.
const ONE_CELL: HourlyCell[] = [{ date: "2026-06-01", hour: 8, avg_min: 2.0, samples: 50 }];

function cellAt(container: HTMLElement, date: string, hour: number): Element {
  return container.querySelector(`[data-testid='heat-cell'][data-date='${date}'][data-hour='${hour}']`)!;
}

describe("HourlyHeatmap tooltip", () => {
  it("formats the tooltip's date and minutes for the locale", () => {
    const { container } = renderHeatmap([{ date: "2026-06-01", hour: 8, avg_min: 3.25, samples: 2500 }]);
    fireEvent.mouseEnter(cellAt(container, "2026-06-01", 8));
    expect(screen.getByText(/Jun 1, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/3\.3 min/)).toBeInTheDocument();
    expect(screen.getByText(/2,500/)).toBeInTheDocument();
  });
});

describe("HourlyHeatmap legend", () => {
  it("labels the shared delay steps, always shown, with how a cell without data looks", async () => {
    await i18n.changeLanguage("en");
    renderHeatmap(ONE_CELL);
    const legend = screen.getByTestId("delay-scale-legend");
    expect(legend).toHaveTextContent("under 1.5 min");
    expect(legend).toHaveTextContent("3.5–5 min");
    expect(legend).toHaveTextContent("5 min or more");
    expect(legend).toHaveTextContent("No data");
    expect(screen.queryByRole("button", { name: "Show legend" })).toBeNull();
  });
});

describe("HourlyHeatmap shading", () => {
  it("shades every populated cell by the shared delay steps, the same scale as the surface above it", () => {
    const { container } = renderHeatmap([
      { date: "2026-06-01", hour: 8, avg_min: 1.0, samples: 50 },
      { date: "2026-06-01", hour: 9, avg_min: 4.0, samples: 50 },
      { date: "2026-06-01", hour: 10, avg_min: DELAY_THRESHOLDS.severe + 1, samples: 50 },
    ]);
    expect((cellAt(container, "2026-06-01", 8) as SVGElement).style.fill).toBe(delayRampVar(1.0));
    expect((cellAt(container, "2026-06-01", 9) as SVGElement).style.fill).toBe(delayRampVar(4.0));
    expect((cellAt(container, "2026-06-01", 10) as SVGElement).style.fill).toBe(delayRampVar(DELAY_THRESHOLDS.severe + 1));
  });

  it("draws no outline over a heavy cell, so heavy hours never stand out as a red grid", () => {
    const { container } = renderHeatmap([{ date: "2026-06-01", hour: 8, avg_min: DELAY_THRESHOLDS.severe + 3, samples: 50 }]);
    expect(container.querySelectorAll("[data-testid='heat-severe-outline']")).toHaveLength(0);
    expect(container.innerHTML).not.toContain("--delay-severe");
  });

  it("fades every cell in to full opacity, the colour alone carrying the delay", () => {
    const { container } = renderHeatmap(ONE_CELL);
    const cell = cellAt(container, "2026-06-01", 8) as SVGElement;
    expect(cell.style.getPropertyValue("--cell-opacity")).toBe("1");
  });
});

describe("HourlyHeatmap hour labels", () => {
  it("gives each hour a row at least as tall as its 12px label, so the 24 labels never overlap", () => {
    const { container } = renderHeatmap([{ date: "2026-05-18", hour: 8, avg_min: 2, samples: 100 }]);
    const ys = Array.from({ length: 24 }, (_, h) => {
      const label = [...container.querySelectorAll("svg text")].find((n) => n.textContent === String(h) && n.getAttribute("text-anchor") === "end");
      return Number(label!.getAttribute("y"));
    });
    for (let h = 1; h < 24; h++) expect(ys[h] - ys[h - 1]).toBeGreaterThanOrEqual(12);
  });
});

function LocationProbe() {
  return <output data-testid="loc">{useLocation().search}</output>;
}

describe("HourlyHeatmap keyboard access", () => {
  function renderWithProbe() {
    return renderWithProviders(
      <MemoryRouter>
        <HourlyHeatmap cells={ONE_CELL} />
        <LocationProbe />
      </MemoryRouter>,
    );
  }

  it("reaches the hour filter from the keyboard", async () => {
    await i18n.changeLanguage("en");
    renderWithProbe();
    const hour = screen.getByRole("button", { name: "Filter to 8:00" });
    expect(hour).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(hour, { key: "Enter" });
    expect(screen.getByTestId("loc").textContent).toContain("time_band=morning");
  });

  it("reaches the day filter from the keyboard", async () => {
    await i18n.changeLanguage("en");
    renderWithProbe();
    const date = screen.getByRole("button", { name: "Filter to Jun 1, 2026" });
    fireEvent.keyDown(date, { key: " " });
    expect(screen.getByTestId("loc").textContent).toContain("from=2026-06-01");
  });

  it("is a group, not an image, so its buttons stay exposed", async () => {
    await i18n.changeLanguage("en");
    renderWithProbe();
    expect(screen.queryByRole("img", { name: "Time-band delay heatmap" })).toBeNull();
    expect(screen.getByRole("group", { name: "Time-band delay heatmap" })).toBeInTheDocument();
  });
});

describe("HourlyHeatmap cells from the keyboard", () => {
  const CELLS: HourlyCell[] = [
    { date: "2026-06-01", hour: 8, avg_min: 2.0, samples: 50 },
    { date: "2026-06-02", hour: 8, avg_min: 4.5, samples: 20 },
    { date: "2026-06-01", hour: 9, avg_min: 1.0, samples: 10 },
  ];

  function renderGrid() {
    return renderWithProviders(
      <MemoryRouter>
        <HourlyHeatmap cells={CELLS} />
        <LocationProbe />
      </MemoryRouter>,
    );
  }

  it("names each cell with its date, hour, average and sample count, and an empty one as having no data", async () => {
    await i18n.changeLanguage("en");
    renderGrid();
    expect(screen.getByRole("gridcell", { name: "Jun 1, 2026 08:00 · avg 2.0 min · 50 obs." })).toBeInTheDocument();
    expect(screen.getByRole("gridcell", { name: "Jun 2, 2026 09:00 · no data" })).toBeInTheDocument();
  });

  it("is one tab stop, moved between hours and dates by the arrow keys", async () => {
    await i18n.changeLanguage("en");
    const user = userEvent.setup();
    renderGrid();
    const grid = screen.getByRole("grid", { name: "Time-band heatmap" });
    expect(grid.querySelectorAll("[tabindex='0']")).toHaveLength(1);

    const start = screen.getByRole("gridcell", { name: "Jun 1, 2026 08:00 · avg 2.0 min · 50 obs." });
    start.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("gridcell", { name: "Jun 2, 2026 08:00 · avg 4.5 min · 20 obs." })).toHaveFocus();
    await user.keyboard("{ArrowLeft}{ArrowDown}");
    expect(screen.getByRole("gridcell", { name: "Jun 1, 2026 09:00 · avg 1.0 min · 10 obs." })).toHaveFocus();
  });

  it("moves between hours with the up and down arrows when the grid has a single date", async () => {
    await i18n.changeLanguage("en");
    const user = userEvent.setup();
    renderWithProviders(
      <MemoryRouter>
        <HourlyHeatmap cells={CELLS.filter((c) => c.date === "2026-06-01")} />
      </MemoryRouter>,
    );
    screen.getByRole("gridcell", { name: "Jun 1, 2026 08:00 · avg 2.0 min · 50 obs." }).focus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("gridcell", { name: "Jun 1, 2026 09:00 · avg 1.0 min · 10 obs." })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("gridcell", { name: "Jun 1, 2026 08:00 · avg 2.0 min · 50 obs." })).toHaveFocus();
  });

  it("drills to the cell's day and time band on Enter, as a click does", async () => {
    await i18n.changeLanguage("en");
    const user = userEvent.setup();
    renderGrid();
    screen.getByRole("gridcell", { name: "Jun 1, 2026 08:00 · avg 2.0 min · 50 obs." }).focus();
    await user.keyboard("{Enter}");
    const search = screen.getByTestId("loc").textContent ?? "";
    expect(search).toContain("from=2026-06-01");
    expect(search).toContain("time_band=morning");
  });

  it("keeps the focus ring on the focused cell when the grid redraws with fewer dates", async () => {
    await i18n.changeLanguage("en");
    const { container, rerender } = renderGrid();
    const focused = () => container.querySelector("[data-testid='heat-cell'][data-date='2026-06-02'][data-hour='8']")!;
    fireEvent.focus(focused());

    rerender(
      <MemoryRouter>
        <HourlyHeatmap cells={CELLS.filter((c) => c.date === "2026-06-02")} />
        <LocationProbe />
      </MemoryRouter>,
    );

    const ring = container.querySelector("[data-testid='heat-focus-ring']")!;
    expect(Number(ring.getAttribute("y")) + 0.5).toBe(Number(focused().getAttribute("y")));
    expect(Number(ring.getAttribute("x")) + 0.5).toBe(Number(focused().getAttribute("x")));
    expect(focused()).toHaveAttribute("tabindex", "0");
    expect(container.querySelectorAll("[role='gridcell'][tabindex='0']")).toHaveLength(1);
  });

  it("clears the tooltip on blur after the cells are refetched as new objects", async () => {
    await i18n.changeLanguage("en");
    const { rerender } = renderGrid();
    const name = "Jun 2, 2026 08:00 · avg 4.5 min · 20 obs.";
    fireEvent.focus(screen.getByRole("gridcell", { name }));

    rerender(
      <MemoryRouter>
        <HourlyHeatmap cells={CELLS.map((c) => ({ ...c }))} />
        <LocationProbe />
      </MemoryRouter>,
    );
    fireEvent.blur(screen.getByRole("gridcell", { name }));

    expect(screen.queryByText(/4\.5 min · 20 obs\./)).toBeNull();
  });

  it("names a cell with no average as having no data, and shows it no tooltip", async () => {
    await i18n.changeLanguage("en");
    renderWithProviders(
      <MemoryRouter>
        <HourlyHeatmap cells={[...CELLS, { date: "2026-06-02", hour: 9, avg_min: null, samples: 4 }]} />
      </MemoryRouter>,
    );
    const cell = screen.getByRole("gridcell", { name: "Jun 2, 2026 09:00 · no data" });
    fireEvent.focus(cell);
    expect(screen.queryByText(/obs\./)).toBeNull();
  });

  it("shows the cell's tooltip and a focus ring while it holds focus", async () => {
    await i18n.changeLanguage("en");
    const { container } = renderGrid();
    const cell = screen.getByRole("gridcell", { name: "Jun 2, 2026 08:00 · avg 4.5 min · 20 obs." });
    fireEvent.focus(cell);
    expect(screen.getByText(/4\.5 min · 20 obs\./)).toBeInTheDocument();
    expect(container.querySelector("[data-testid='heat-focus-ring']")).not.toBeNull();
    fireEvent.blur(cell);
    expect(container.querySelector("[data-testid='heat-focus-ring']")).toBeNull();
  });
});
