import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RichResult } from "./RichResult";
import type { NextStepAction } from "./nextStepChips";
import type { ToolResult } from "../../api/types";

type WrapperProps = {
  result: ToolResult;
  tool?: string | null;
  args?: Record<string, unknown> | null;
  conditions?: { dow?: string; time_band?: string; service?: string } | null;
  onChip?: (action: NextStepAction) => void;
};

function Wrapper({ result, tool = "top_n", args = {}, conditions = null, onChip }: WrapperProps) {
  const { t } = useTranslation();
  return (
    <RichResult
      result={result}
      fallbackText="fallback"
      formatRoute={(rc) => rc ?? ""}
      t={t}
      tool={tool}
      args={args}
      conditions={conditions}
      onChip={onChip}
    />
  );
}

const tableResult: ToolResult = {
  kind: "table",
  summary: "遅延ランキングの要約",
  rows: [["39061", "平日", 5.2, 3.1, 8.4, 120]],
  columns: ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"],
};

const seriesResult: ToolResult = {
  kind: "series",
  summary: "推移の要約",
  series: [
    { date: "2026-08-01", avg_min: 4.1, samples: 30 },
    { date: "2026-08-02", avg_min: 4.4, samples: 28 },
  ] as unknown as ToolResult["series"],
};

const t: TFunction = i18n.getFixedT("en");

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

describe("RichResult evidence card", () => {
  it("shows the SQL-only badge for a dispatched (tool-bearing) message", () => {
    renderWithProviders(<Wrapper result={tableResult} tool="top_n" />);
    expect(screen.getByText(t("ask.evidence.badge.sql"))).toBeInTheDocument();
    expect(screen.queryByText(t("ask.evidence.badge.llm"))).not.toBeInTheDocument();
  });

  it("shows the LLM badge for a dispatch-free message", () => {
    renderWithProviders(<Wrapper result={tableResult} tool={null} />);
    expect(screen.getByText(t("ask.evidence.badge.llm"))).toBeInTheDocument();
  });

  it("renders the chart/table before the one-sentence summary", () => {
    const { container } = renderWithProviders(<Wrapper result={tableResult} />);
    const table = container.querySelector("table");
    const summary = screen.getByText(tableResult.summary);
    expect(table).toBeInTheDocument();
    // DOCUMENT_POSITION_FOLLOWING (4) means `summary` comes after `table`.
    expect(table!.compareDocumentPosition(summary)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("renders the one-sentence summary using the display font token", () => {
    renderWithProviders(<Wrapper result={tableResult} />);
    const summary = screen.getByText(tableResult.summary);
    expect(summary).toHaveStyle({ fontFamily: "var(--font-display)" });
  });

  it("renders next-step chips and forwards a tap to onChip", async () => {
    const onChip = vi.fn();
    renderWithProviders(
      <Wrapper result={{ ...tableResult }} tool="segment_hotspots" args={{ route: "A05" }} onChip={onChip} />,
    );
    const chip = screen.getByRole("button", { name: t("ask.evidence.chip.view_map") });
    await userEvent.click(chip);
    expect(onChip).toHaveBeenCalledWith({ kind: "map", route: "A05" });
  });

  it("renders no chips for a dispatch-free message", () => {
    renderWithProviders(<Wrapper result={tableResult} tool={null} />);
    expect(screen.queryByText(t("ask.evidence.chip.morning_peak"))).not.toBeInTheDocument();
  });

  it("renders the provenance disclosure with route, conditions, aggregation and confidence", () => {
    renderWithProviders(
      <Wrapper
        result={tableResult}
        tool="segment_hotspots"
        args={{ route: "A05" }}
        conditions={{ dow: "weekend", time_band: "all", service: "all" }}
      />,
    );
    expect(screen.getByText(t("ask.evidence.disclosure_title"))).toBeInTheDocument();
    expect(screen.getByText("A05")).toBeInTheDocument();
    expect(screen.getByText(t("filters.dow.weekend"))).toBeInTheDocument();
    expect(screen.getAllByText(t("ask.evidence.tool_label.segment_hotspots")).length).toBeGreaterThan(0);
    expect(screen.getByText(t("ask.evidence.confidence.sql"))).toBeInTheDocument();
  });

  it("shows the LLM confidence copy for a dispatch-free message", () => {
    renderWithProviders(<Wrapper result={tableResult} tool={null} />);
    expect(screen.getByText(t("ask.evidence.confidence.llm"))).toBeInTheDocument();
  });

  it("does not wrap a plain-text fallback result in evidence-card chrome", () => {
    renderWithProviders(<Wrapper result={{ kind: "empty", summary: "" }} />);
    expect(screen.getByText("fallback")).toBeInTheDocument();
    expect(screen.queryByText(t("ask.evidence.disclosure_title"))).not.toBeInTheDocument();
    expect(screen.queryByText(t("ask.evidence.badge.sql"))).not.toBeInTheDocument();
  });

  it("exports the chart as PNG locally for the export_png chip, without requiring onChip", async () => {
    const chartPng = await import("./chartPng");
    const spy = vi.spyOn(chartPng, "exportSvgAsPng").mockImplementation(() => {});
    renderWithProviders(<Wrapper result={seriesResult} tool="time_series" />);
    const chip = screen.getByRole("button", { name: t("ask.evidence.chip.export_png") });
    await userEvent.click(chip);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBeInstanceOf(SVGElement);
  });
});
