import { afterEach, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { buildCardTemplates } from "./askCardTemplates";

function summary(id: string, values: Record<string, unknown>) {
  const tpl = buildCardTemplates().find((card) => card.id === id);
  if (!tpl) throw new Error(`no ${id} card`);
  return tpl.buildSummary(values, i18n.t.bind(i18n));
}

describe("ranking card summaries, which also become the question in the conversation", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("reads as a request, naming the service only when one is chosen", () => {
    expect(summary("top_delay", { k: 5, service_type: "all" })).toBe("5 most delayed routes");
    expect(summary("top_delay", { k: 5, service_type: "weekday" })).toBe("5 most delayed routes on weekdays");
    expect(summary("top_delay", { k: 8, service_type: "weekend" })).toBe("8 most delayed routes on weekends and holidays");
    expect(summary("ontime_rank", { k: 5, best_first: "false" })).toBe("5 least on-time routes");
    expect(summary("ontime_rank", { k: 5, best_first: "true" })).toBe("5 most on-time routes");
  });

  it("leaves an all-services ranking unqualified in Japanese too", async () => {
    await i18n.changeLanguage("ja");
    expect(summary("top_delay", { k: 5, service_type: "all" })).toBe("遅延の大きい路線：上位5件"); // i18n-ignore: expected copy
    expect(summary("top_delay", { k: 5, service_type: "weekday" })).toBe("遅延の大きい路線：上位5件（平日）"); // i18n-ignore: expected copy
  });
});
