import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Skeleton, SkeletonKpiRow, SkeletonChart, SkeletonTable } from "./Skeleton";

function bars(container: HTMLElement) {
  return container.querySelectorAll(".skeleton");
}

describe("Skeleton", () => {
  it("renders a single shimmer block at the requested size", () => {
    const { container } = render(<Skeleton height={48} width="60%" />);
    const block = container.querySelector(".skeleton");
    expect(block).toHaveStyle({ height: "48px", width: "60%" });
  });
});

describe("SkeletonKpiRow", () => {
  it("stands in for a row of KPI tiles, each with its own label and value bar", () => {
    const { container } = render(<SkeletonKpiRow />);
    expect(screen.getAllByTestId("skeleton-kpi-tile")).toHaveLength(3);
    // Two bars per tile — a short caption over a tall number — so the
    // placeholder has the tile's geometry rather than one flat slab.
    expect(bars(container)).toHaveLength(6);
  });

  it("honours a caller-supplied tile count", () => {
    render(<SkeletonKpiRow tiles={4} />);
    expect(screen.getAllByTestId("skeleton-kpi-tile")).toHaveLength(4);
  });
});

describe("SkeletonChart", () => {
  it("reserves the plot's real height under a caption bar", () => {
    render(<SkeletonChart height={260} />);
    expect(screen.getByTestId("skeleton-chart-plot")).toHaveStyle({ height: "260px" });
  });
});

describe("SkeletonTable", () => {
  it("renders one bar per expected row", () => {
    const { container } = render(<SkeletonTable rows={5} />);
    expect(bars(container)).toHaveLength(5);
  });

  it("matches the real row height when the caller knows it", () => {
    render(<SkeletonTable rows={2} rowHeight={48} />);
    for (const row of screen.getAllByTestId("skeleton-table-row")) {
      expect(row).toHaveStyle({ height: "48px" });
    }
  });
});

describe("every skeleton", () => {
  it("is hidden from assistive technology — it carries no information", () => {
    for (const ui of [<SkeletonKpiRow key="k" />, <SkeletonChart key="c" />, <SkeletonTable key="t" />]) {
      const { container, unmount } = render(ui);
      expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
      unmount();
    }
  });
});
