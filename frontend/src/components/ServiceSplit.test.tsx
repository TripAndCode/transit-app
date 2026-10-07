import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { ServiceSplit } from "./ServiceSplit";

describe("ServiceSplit", () => {
  it("names the difference between the two service days in words", () => {
    render(<ServiceSplit service_split={{ 平日: 2.6, 土日祝: 2.5 }} />); // i18n-ignore: GTFS service-type keys
    expect(screen.getByText("Difference: 0.1 min (4%)")).toBeInTheDocument();
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
