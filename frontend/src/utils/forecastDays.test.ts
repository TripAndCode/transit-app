import { describe, it, expect } from "vitest";
import i18n from "../i18n";
import { datedDayLabel } from "./forecastDays";

const WEEK_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

describe("datedDayLabel", () => {
  it("adds its date to a weekday the forecast model covers, and leaves the others as weekdays", async () => {
    await i18n.changeLanguage("en");
    const label = datedDayLabel(i18n.t, (dow) => WEEK_LABELS[dow - 1], [
      { dow: 2, service_date: "2026-10-27" },
      { dow: 7, service_date: "2026-11-01" },
    ]);
    expect(label(2)).toBe("Tue 10/27");
    expect(label(7)).toBe("Sun 11/1");
    expect(label(1)).toBe("Mon");
  });
});
