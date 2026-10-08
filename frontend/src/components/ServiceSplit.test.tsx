import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { ServiceSplit } from "./ServiceSplit";

describe("ServiceSplit", () => {
  it("names the difference between the two service days in words", () => {
    render(<ServiceSplit service_split={{ 平日: 2.6, 土日祝: 2.5 }} />); // i18n-ignore: GTFS service-type keys
    expect(screen.getByText("Difference: 0.1 min (4%)")).toBeInTheDocument();
  });
});

describe("ServiceSplit modal chart tooltip", () => {
  it("places its tooltip in the plot box it measures from, not the wrap that also holds the legend", () => {
    const { container } = renderWithProviders(
      <ServiceSplit
        variant="modal"
        service_split={{ 平日: 2.0, 土日祝: 1.0 }}
        daily={[
          { date: "2026-09-05", weekday: 2.0, weekend: 1.0 },
          { date: "2026-09-06", weekday: 2.2, weekend: 1.1 },
        ]}
      />,
    );
    const svg = container.querySelector(".ov-chart-plot svg") as SVGSVGElement;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.mouseMove(svg, { clientX: 300, clientY: 100 });
    expect(container.querySelector(".ov-tooltip")?.closest(".ov-chart-plot")).not.toBeNull();
  });
});

describe("ServiceSplit modal chart", () => {
  it("labels the x axis with locale short dates", () => {
    renderWithProviders(
      <ServiceSplit
        variant="modal"
        service_split={{ 平日: 2.0, 土日祝: 1.0 }}
        daily={[
          { date: "2026-09-05", weekday: 2.0, weekend: 1.0 },
          { date: "2026-09-06", weekday: 2.2, weekend: 1.1 },
        ]}
      />,
    );
    expect(screen.getByText("9/5")).toBeInTheDocument();
    expect(screen.getByText("9/6")).toBeInTheDocument();
  });
});
