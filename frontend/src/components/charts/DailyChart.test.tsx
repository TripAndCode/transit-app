import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { DailyChart } from "./DailyChart";
import { DELAY_THRESHOLDS } from "../../styles/tokens";
import type { TrendDay } from "../../api/types";

function day(overrides: Partial<TrendDay> & { date: string }): TrendDay {
  return {
    avg_min: 2.0,
    samples: 100,
    top_offenders: [],
    ...overrides,
  };
}

/** Renders the current query string so a test can assert what the brush wrote
 *  into the shared range context. */
function RangeProbe() {
  const [params] = useSearchParams();
  return <output data-testid="range">{`${params.get("from") ?? ""}..${params.get("to") ?? ""}`}</output>;
}

function renderChart(ui: React.ReactElement) {
  return renderWithProviders(
    <MemoryRouter>
      {ui}
      <RangeProbe />
    </MemoryRouter>,
  );
}

const WEEK_DAYS = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"];

function dayRects(container: HTMLElement) {
  return Array.from(container.querySelectorAll("[data-testid='daily-day']"));
}

describe("DailyChart", () => {
  it("renders the empty state with no days", () => {
    renderChart(<DailyChart days={[]} />);
    expect(screen.getByText(i18n.t("reports.daily.empty"))).toBeInTheDocument();
  });

  it("does not show the smoothed-average legend when no day has a smoothed value", () => {
    renderChart(
      <DailyChart days={[day({ date: "2026-05-18", avg_min_smoothed: null }), day({ date: "2026-05-19" })]} />,
    );
    expect(screen.queryByText(i18n.t("reports.daily.smoothed_label"))).not.toBeInTheDocument();
  });

  it("shows the smoothed-average legend when at least one day has a smoothed value", () => {
    renderChart(
      <DailyChart
        days={[
          day({ date: "2026-05-18", avg_min: 1.0, avg_min_smoothed: 1.0 }),
          day({ date: "2026-05-19", avg_min: 3.0, avg_min_smoothed: 2.0 }),
        ]}
      />,
    );
    expect(screen.getByText(i18n.t("reports.daily.smoothed_label"))).toBeInTheDocument();
  });

  it("marks a schedule-revision boundary date that appears in days", () => {
    renderChart(
      <DailyChart
        days={[day({ date: "2026-05-18" }), day({ date: "2026-05-19" }), day({ date: "2026-05-20" })]}
        revisionBoundaries={["2026-05-19"]}
      />,
    );
    expect(screen.getByText(i18n.t("reports.daily.revision_boundary_label"))).toBeInTheDocument();
  });

  it("skips a boundary date absent from days without crashing", () => {
    renderChart(
      <DailyChart
        days={[day({ date: "2026-05-18" }), day({ date: "2026-05-19" })]}
        revisionBoundaries={["2099-01-01"]}
      />,
    );
    expect(screen.queryByText(i18n.t("reports.daily.revision_boundary_label"))).not.toBeInTheDocument();
  });

  it("draws the line in via ChartEnter's useDrawOn", () => {
    // jsdom implements no SVG geometry interfaces -- every SVG element it
    // creates is a plain SVGElement, so the mock goes on that prototype.
    (SVGElement.prototype as unknown as { getTotalLength: () => number }).getTotalLength = () => 842;
    const { container } = renderChart(
      <DailyChart days={[day({ date: "2026-05-18" }), day({ date: "2026-05-19" })]} />,
    );
    const polylines = container.querySelectorAll("polyline");
    const line = polylines[polylines.length - 1]; // the raw-average line, always last
    expect(line.classList.contains("chart-draw-on")).toBe(true);
    expect(Number(line.style.getPropertyValue("--len"))).toBe(842);
    delete (SVGElement.prototype as unknown as { getTotalLength?: () => number }).getTotalLength;
  });
});

describe("DailyChart annotations", () => {
  it("bands the severe region when the plotted maximum reaches it", () => {
    const { container } = renderChart(
      <DailyChart
        days={[day({ date: "2026-05-18", avg_min: 1 }), day({ date: "2026-05-19", avg_min: DELAY_THRESHOLDS.severe + 3 })]}
      />,
    );
    expect(container.querySelector("[data-testid='threshold-band']")).not.toBeNull();
    expect(screen.getByText(i18n.t("reports.daily.severe_band_label", { min: DELAY_THRESHOLDS.severe }))).toBeInTheDocument();
  });

  it("omits the severe band entirely when no day comes close to it", () => {
    const { container } = renderChart(
      <DailyChart days={[day({ date: "2026-05-18", avg_min: 0.5 }), day({ date: "2026-05-19", avg_min: 1 })]} />,
    );
    expect(container.querySelector("[data-testid='threshold-band']")).toBeNull();
  });

  it("marks the worst day through the same VerticalMarker primitive as a revision boundary", () => {
    const { container } = renderChart(
      <DailyChart
        days={[day({ date: "2026-05-18", avg_min: 1 }), day({ date: "2026-05-19", avg_min: 9 }), day({ date: "2026-05-20", avg_min: 2 })]}
        revisionBoundaries={["2026-05-20"]}
      />,
    );
    expect(container.querySelector("[data-variant='highlight']")).not.toBeNull();
    expect(container.querySelector("[data-variant='context']")).not.toBeNull();
    expect(screen.getByText(i18n.t("reports.daily.worst_day_label"))).toBeInTheDocument();
  });
});

describe("DailyChart brush", () => {
  const days = WEEK_DAYS.map((date) => day({ date }));

  it("writes the dragged span into the range context on release", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const rects = dayRects(container);
    fireEvent.mouseDown(rects[1]);
    fireEvent.mouseEnter(rects[3]);
    fireEvent.mouseUp(screen.getByRole("slider"));
    expect(screen.getByTestId("range")).toHaveTextContent("2026-05-19..2026-05-21");
  });

  it("normalises a right-to-left drag", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const rects = dayRects(container);
    fireEvent.mouseDown(rects[4]);
    fireEvent.mouseEnter(rects[2]);
    fireEvent.mouseUp(screen.getByRole("slider"));
    expect(screen.getByTestId("range")).toHaveTextContent("2026-05-20..2026-05-22");
  });

  it("shades the days under an in-progress drag", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const rects = dayRects(container);
    fireEvent.mouseDown(rects[1]);
    fireEvent.mouseEnter(rects[3]);
    expect(container.querySelector("[data-testid='shaded-days']")).not.toBeNull();
  });

  it("leaves the range alone for a click with no drag", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const rects = dayRects(container);
    fireEvent.mouseDown(rects[2]);
    fireEvent.mouseUp(screen.getByRole("slider"));
    expect(screen.getByTestId("range")).toHaveTextContent("..");
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();
  });

  it("offers a reset chip only once a brush has set the range", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const resetName = i18n.t("reports.daily.brush_reset");
    expect(screen.queryByRole("button", { name: resetName })).toBeNull();

    const rects = dayRects(container);
    fireEvent.mouseDown(rects[0]);
    fireEvent.mouseEnter(rects[2]);
    fireEvent.mouseUp(screen.getByRole("slider"));
    fireEvent.click(screen.getByRole("button", { name: resetName }));

    expect(screen.getByTestId("range")).toHaveTextContent("..");
    expect(screen.queryByRole("button", { name: resetName })).toBeNull();
  });

  it("extends the selection with Shift+ArrowRight and applies it on Enter", () => {
    renderChart(<DailyChart days={days} />);
    const surface = screen.getByRole("slider");
    fireEvent.keyDown(surface, { key: "ArrowRight" });
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(screen.getByTestId("range")).toHaveTextContent("2026-05-19..2026-05-21");
  });

  it("exposes no brush at all for a non-brushable embed", () => {
    const { container } = renderChart(<DailyChart days={days} brushable={false} />);
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByText(i18n.t("reports.daily.brush_hint"))).toBeNull();
    fireEvent.mouseDown(dayRects(container)[0]);
    fireEvent.mouseEnter(dayRects(container)[2]);
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();
  });

  // The same hazard the hover index is clamped for: a filter change can
  // shrink `days` while a selection is still open, and the endpoints are
  // indices into the array that just got shorter.
  it("drops a selection whose days no longer exist rather than indexing past the end", () => {
    const { container, rerender } = renderChart(<DailyChart days={days} />);
    const surface = screen.getByRole("slider");
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    expect(container.querySelector("[data-testid='shaded-days']")).not.toBeNull();

    // Narrowing another filter refetches the same query with fewer days.
    expect(() =>
      rerender(
        <MemoryRouter>
          <DailyChart days={days.slice(0, 1)} />
          <RangeProbe />
        </MemoryRouter>,
      ),
    ).not.toThrow();
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", WEEK_DAYS[0]);
  });

  // Dropping the selection at render time does not clear the state behind
  // it, so a drag abandoned while the data was short must not come back as a
  // range the user never drew once the data widens again.
  it("does not revive a dropped drag when the days come back", () => {
    const { container, rerender } = renderChart(<DailyChart days={days} />);
    fireEvent.mouseDown(dayRects(container)[0]);
    fireEvent.mouseEnter(dayRects(container)[3]);
    expect(container.querySelector("[data-testid='shaded-days']")).not.toBeNull();

    const short = (
      <MemoryRouter>
        <DailyChart days={days.slice(0, 2)} />
        <RangeProbe />
      </MemoryRouter>
    );
    rerender(short);
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();

    // Hovering while the selection is dropped must not extend it, and the
    // widened data must not bring the abandoned anchor back.
    fireEvent.mouseEnter(dayRects(container)[1]);
    rerender(
      <MemoryRouter>
        <DailyChart days={days} />
        <RangeProbe />
      </MemoryRouter>,
    );
    expect(container.querySelector("[data-testid='shaded-days']") === null).toBe(true);
    expect(screen.getByTestId("range").textContent).toBe("..");
  });

  // The other way out of a dropped drag: the pointer leaves the chart, which
  // commits. That path must clear the state behind the dropped selection too,
  // or widening the data later resurrects it and commits a range unprompted.
  it("does not revive a dropped drag after the pointer leaves the chart", () => {
    const { container, rerender } = renderChart(<DailyChart days={days} />);
    fireEvent.mouseDown(dayRects(container)[0]);
    fireEvent.mouseEnter(dayRects(container)[3]);

    const widen = (d: typeof days) => (
      <MemoryRouter>
        <DailyChart days={d} />
        <RangeProbe />
      </MemoryRouter>
    );
    rerender(widen(days.slice(0, 2)));
    fireEvent.mouseLeave(screen.getByRole("slider"));
    rerender(widen(days));

    expect(container.querySelector("[data-testid='shaded-days']") === null).toBe(true);
    expect(screen.getByTestId("range").textContent).toBe("..");
  });

  it("drops an in-progress keyboard selection on Escape", () => {
    const { container } = renderChart(<DailyChart days={days} />);
    const surface = screen.getByRole("slider");
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    expect(container.querySelector("[data-testid='shaded-days']")).not.toBeNull();
    fireEvent.keyDown(surface, { key: "Escape" });
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();
  });
});
