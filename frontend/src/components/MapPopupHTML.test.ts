import { describe, it, expect } from "vitest";
import { renderStopPopupHTML } from "./MapPopupHTML";

// Fake t — returns the key so assertions can check which strings render.
const t = ((k: string) => k) as never;
const period = { from: "2026-01-01", to: "2026-01-31" };

describe("renderStopPopupHTML", () => {
  it("shows avg + samples for an observed stop", () => {
    const html = renderStopPopupHTML({ stop_name: "Obs", avg_min: 4.2, samples: 120 }, period, t);
    expect(html).toContain("Obs");
    expect(html).toContain("map.popup.avg_delay_label");
    expect(html).toContain("4.2");
    expect(html).toContain("map.popup.samples_label");
    expect(html).toContain("120");
    expect(html).not.toContain("map.popup.no_data");
  });

  it("shows a no-measurements line (not 0.0/0) for an unobserved stop", () => {
    const html = renderStopPopupHTML(
      { stop_name: "Unobs", avg_min: 0, samples: 0, stop_sequence: 3, active_route: "R1" },
      period,
      t,
    );
    expect(html).toContain("Unobs"); // name still shown
    expect(html).toContain("map.popup.stop_seq_prefix"); // meta line still shown
    expect(html).toContain("map.popup.no_data");
    expect(html).not.toContain("map.popup.avg_delay_label");
    expect(html).not.toContain("map.popup.samples_label");
  });
});
