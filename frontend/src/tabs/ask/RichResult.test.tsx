import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RichResult } from "./RichResult";
import type { ToolResult } from "../../api/types";

function Wrapper({ result }: { result: ToolResult }) {
  const { t } = useTranslation();
  return <RichResult result={result} fallbackText="fallback" formatRoute={(rc) => rc ?? ""} t={t} />;
}

describe("RichResult on_time low_confidence column", () => {
  it("renders a caveat marker for a low-confidence row", () => {
    renderWithProviders(
      <Wrapper
        result={{
          kind: "table",
          summary: "On-time rate",
          rows: [["39061", "平日", 80.0, 0.5, 25, true]],
          columns: ["route_code", "service_type", "on_time_pct", "avg_min", "samples", "low_confidence"],
        }}
      />,
    );
    expect(screen.getByText(i18n.t("ask.low_confidence_mark"))).toBeInTheDocument();
  });

  it("renders no caveat marker for a confident row", () => {
    renderWithProviders(
      <Wrapper
        result={{
          kind: "table",
          summary: "On-time rate",
          rows: [["39061", "平日", 90.0, 0.5, 300, false]],
          columns: ["route_code", "service_type", "on_time_pct", "avg_min", "samples", "low_confidence"],
        }}
      />,
    );
    expect(screen.queryByText(i18n.t("ask.low_confidence_mark"))).not.toBeInTheDocument();
    // The raw boolean must never leak through as literal text.
    expect(screen.queryByText("false")).not.toBeInTheDocument();
  });

  it("does not special-case any column when low_confidence is absent", () => {
    renderWithProviders(
      <Wrapper
        result={{
          kind: "table",
          summary: "Delay ranking",
          rows: [["39061", "平日", 5.2, 3.1, 8.4, 120]],
          columns: ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"],
        }}
      />,
    );
    expect(screen.getByText("120")).toBeInTheDocument();
  });
});
