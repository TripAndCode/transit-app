import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../i18n";
import { ServiceSplit } from "./ServiceSplit";

describe("ServiceSplit", () => {
  it("names the difference between the two service days in words", () => {
    render(<ServiceSplit service_split={{ 平日: 2.6, 土日祝: 2.5 }} />); // i18n-ignore: GTFS service-type keys
    expect(screen.getByText("Difference: 0.1 min (4%)")).toBeInTheDocument();
  });
});
