import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
