import { describe, it, expect } from "vitest";
import i18n from "../i18n";
import { dowValueLabel, serviceValueLabel, timeBandValueLabel } from "./filterValueLabels";

const t = i18n.getFixedT("en");
const tDesignNs = i18n.getFixedT("en", "design");

describe("filter value labels", () => {
  it("maps dow wire values onto the shared service-value copy", () => {
    expect(dowValueLabel("weekday", t)).toBe("Weekday");
    expect(dowValueLabel("weekend", t)).toBe("Weekend/Holiday");
  });

  it("names each day of a weekday list, in both languages", () => {
    expect(dowValueLabel("mon,wed", t)).toBe("Mon, Wed");
    expect(dowValueLabel("sat", t)).toBe("Sat");
    expect(dowValueLabel("mon,wed", i18n.getFixedT("ja"))).toBe("月・水");
  });

  it("maps service wire values onto their display copy", () => {
    expect(serviceValueLabel("平日", t)).toBe("Weekday"); // i18n-ignore: query contract
    expect(serviceValueLabel("土日祝", t)).toBe("Weekend/Holiday"); // i18n-ignore: query contract
  });

  it("translates the calendar names agencies commonly give their services", () => {
    expect(serviceValueLabel("秋彼岸", t)).toBe("Autumn equinox (Higan)"); // i18n-ignore: GTFS service name
    expect(serviceValueLabel("祝日", t)).toBe("Holiday"); // i18n-ignore: GTFS service name
    expect(serviceValueLabel("秋彼岸", i18n.getFixedT("ja"))).toBe("秋彼岸"); // i18n-ignore: GTFS service name
  });

  it("renders unknown values raw instead of guessing a known label", () => {
    expect(dowValueLabel("holiday", t)).toBe("holiday");
    expect(serviceValueLabel("お盆臨時　20日", t)).toBe("お盆臨時　20日"); // i18n-ignore: an operator's own service name
    expect(timeBandValueLabel("dawn", t)).toBe("dawn");
  });

  it("resolves real copy for a caller bound to a non-translation default namespace", () => {
    expect(dowValueLabel("weekday", tDesignNs)).toBe("Weekday");
    expect(serviceValueLabel("土日祝", tDesignNs)).toBe("Weekend/Holiday"); // i18n-ignore: query contract
    expect(timeBandValueLabel("morning", tDesignNs)).toBe(t("filters.time_band.morning"));
  });
});
