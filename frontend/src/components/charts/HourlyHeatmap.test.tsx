import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { HourlyHeatmap, type HourlyCell } from "./HourlyHeatmap";
import { DELAY_THRESHOLDS, HEAT_RAMP, heatOpacity } from "../../styles/tokens";
import i18n from "../../i18n";

function renderHeatmap(cells: HourlyCell[]) {
  return renderWithProviders(
    <MemoryRouter>
      <HourlyHeatmap cells={cells} />
    </MemoryRouter>,
  );
}

// A single non-empty cell is enough to skip the empty-state early return and
// reach the legend toggle.
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
  it("describes one accent ramp over the delay domain instead of four colour bands", async () => {
    await i18n.changeLanguage("en");
    renderHeatmap(ONE_CELL);
    fireEvent.click(screen.getByRole("button", { name: i18n.t("reports.heatmap.legend_aria") }));

    expect(screen.getByText(i18n.t("reports.heatmap.ramp_max", { min: HEAT_RAMP.maxMin }))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t("reports.heatmap.severe_outline", { min: DELAY_THRESHOLDS.severe })),
    ).toBeInTheDocument();

    // None of the four-colour band labels survive.
    expect(screen.queryByText("1.5–3 min")).toBeNull();
    expect(screen.queryByText("3–5 min")).toBeNull();
    expect(screen.queryByText("> 5 min")).toBeNull();
  });
});

describe("HourlyHeatmap ramp", () => {
  it("paints every populated cell in one hue, varying only its opacity", () => {
    const { container } = renderHeatmap([
      { date: "2026-06-01", hour: 8, avg_min: 1.0, samples: 50 },
      { date: "2026-06-01", hour: 9, avg_min: 5.0, samples: 50 },
    ]);
    const light = cellAt(container, "2026-06-01", 8) as SVGElement;
    const dark = cellAt(container, "2026-06-01", 9) as SVGElement;

    expect(light.style.fill).toBe("var(--accent)");
    expect(dark.style.fill).toBe("var(--accent)");
    expect(light.getAttribute("opacity")).toBe(String(heatOpacity(1.0)));
    expect(dark.getAttribute("opacity")).toBe(String(heatOpacity(5.0)));
    expect(heatOpacity(5.0)).toBeGreaterThan(heatOpacity(1.0));
  });

  it("carries the ramp value as --cell-opacity so the entrance fade lands on it", () => {
    const { container } = renderHeatmap(ONE_CELL);
    const cell = cellAt(container, "2026-06-01", 8) as SVGElement;
    expect(cell.style.getPropertyValue("--cell-opacity")).toBe(String(heatOpacity(2.0)));
  });

  it("outlines a cell over the severe threshold rather than recolouring it", () => {
    const { container } = renderHeatmap([
      { date: "2026-06-01", hour: 8, avg_min: DELAY_THRESHOLDS.severe + 1, samples: 50 },
      { date: "2026-06-01", hour: 9, avg_min: 1.0, samples: 50 },
    ]);
    const outlines = container.querySelectorAll("[data-testid='heat-severe-outline']");
    expect(outlines).toHaveLength(1);
    expect(outlines[0].getAttribute("stroke")).toBe("var(--delay-severe)");
    expect(outlines[0].getAttribute("fill")).toBe("none");
    // The cell itself is still the single accent hue.
    expect((cellAt(container, "2026-06-01", 8) as SVGElement).style.fill).toBe("var(--accent)");
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

  it("tells assistive tech whether the legend is open", async () => {
    await i18n.changeLanguage("en");
    renderWithProbe();
    const toggle = screen.getByRole("button", { name: "Show legend" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
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
