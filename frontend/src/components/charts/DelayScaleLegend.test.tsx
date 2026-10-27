import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "../../i18n";
import { DelayScaleLegend } from "./DelayScaleLegend";
import { delayScaleSteps } from "./delayScale";
import { DELAY_RAMP_STEPS } from "../../styles/tokens";

describe("delayScaleSteps", () => {
  it("labels every step of the shared ramp with the minutes it colours", () => {
    expect(delayScaleSteps(i18n.getFixedT("en")).map((s) => s.label)).toEqual([
      "under 1.5 min",
      "1.5–2.5 min",
      "2.5–3.5 min",
      "3.5–5 min",
      "5 min or more",
    ]);
    expect(delayScaleSteps(i18n.getFixedT("ja")).map((s) => s.label)).toEqual([
      "1.5分未満", // i18n-ignore: asserting the ja copy
      "1.5〜2.5分", // i18n-ignore: asserting the ja copy
      "2.5〜3.5分", // i18n-ignore: asserting the ja copy
      "3.5〜5分", // i18n-ignore: asserting the ja copy
      "5分以上", // i18n-ignore: asserting the ja copy
    ]);
    expect(delayScaleSteps(i18n.getFixedT("en")).map((s) => s.color)).toEqual(DELAY_RAMP_STEPS.map((s) => s.color));
  });
});

describe("DelayScaleLegend", () => {
  it("adds the no-data fill only when the view draws one", () => {
    const { rerender } = render(<DelayScaleLegend />);
    expect(screen.queryByText("No data")).toBeNull();
    rerender(<DelayScaleLegend noData />);
    expect(screen.getByText("No data")).toBeInTheDocument();
  });
});
