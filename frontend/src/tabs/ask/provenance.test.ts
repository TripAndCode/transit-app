import { describe, it, expect } from "vitest";
import i18n from "../../i18n";
import {
  conditionsLabel,
  formatWindow,
  provenancePath,
  sampleCount,
  toolLabel,
} from "./provenance";

const t = i18n.getFixedT("en");
const tJa = i18n.getFixedT("ja");

describe("provenancePath", () => {
  it("is 'sql' for a message with a tool (deterministic dispatch)", () => {
    expect(provenancePath({ tool: "top_n" })).toBe("sql");
  });

  it("is 'llm' for a message with no tool (LLM-grounded follow-up)", () => {
    expect(provenancePath({ tool: null })).toBe("llm");
  });
});

describe("toolLabel", () => {
  it("returns null for a message with no tool", () => {
    expect(toolLabel(null, t)).toBeNull();
  });

  it("labels a builder tool reusing the existing build_labels.tools map", () => {
    expect(toolLabel("top_n", t)).toBe(i18n.getFixedT("en")("ask.build_labels.tools.top_n"));
  });

  it("labels the card aliases (trend/cmp_service/on_time) via their canonical tool", () => {
    expect(toolLabel("trend", t)).toBe(t("ask.build_labels.tools.time_series"));
    expect(toolLabel("cmp_service", t)).toBe(t("ask.build_labels.tools.compare_segments"));
    expect(toolLabel("on_time", t)).toBe(t("ask.evidence.tool_label.on_time_rate"));
  });

  it("labels tools with no build_labels entry via the evidence-specific map", () => {
    expect(toolLabel("route_stop_patterns", t)).toBe(t("ask.evidence.tool_label.route_stop_patterns"));
    expect(toolLabel("segment_hotspots", t)).toBe(t("ask.evidence.tool_label.segment_hotspots"));
  });

  it("falls back to the raw tool name for an unrecognized tool", () => {
    expect(toolLabel("some_future_tool", t)).toBe("some_future_tool");
  });
});

describe("formatWindow", () => {
  it("returns null when args has no from_date/to_date", () => {
    expect(formatWindow(null, t)).toBeNull();
    expect(formatWindow({}, t)).toBeNull();
  });

  it("formats an explicit from_date/to_date range", () => {
    expect(formatWindow({ from_date: "2026-08-01", to_date: "2026-08-31" }, t)).toBe(
      t("ask.evidence.window_range", { from: "2026-08-01", to: "2026-08-31" }),
    );
  });
});

describe("sampleCount", () => {
  it("returns null for a null result", () => {
    expect(sampleCount(null)).toBeNull();
  });

  it("counts table rows", () => {
    expect(sampleCount({ rows: [[1], [2], [3]] })).toBe(3);
  });

  it("counts series points when there are no rows", () => {
    expect(sampleCount({ series: [{ date: "2026-01-01" }, { date: "2026-01-02" }] })).toBe(2);
  });

  it("returns null for a kv/text result with neither", () => {
    expect(sampleCount({})).toBeNull();
  });
});

describe("conditionsLabel", () => {
  it("says 'all conditions' when there are no conditions or every dim is 'all'", () => {
    expect(conditionsLabel(null, t)).toBe(t("ask.evidence.conditions_all"));
    expect(conditionsLabel({ dow: "all", time_band: "all", service: "all" }, t)).toBe(t("ask.evidence.conditions_all"));
  });

  it("joins the non-default dims", () => {
    expect(conditionsLabel({ dow: "weekend", time_band: "morning", service: "all" }, t)).toBe(
      `${t("common.service_value.土日祝")} · ${t("filters.time_band.morning")}`,
    );
  });

  it("renders in the active locale", () => {
    expect(conditionsLabel({ dow: "weekday", time_band: "all", service: "all" }, tJa)).toBe(tJa("common.service_value.平日"));
  });
});
