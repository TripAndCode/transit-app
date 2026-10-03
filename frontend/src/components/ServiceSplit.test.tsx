import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { ServiceSplit } from "./ServiceSplit";

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
