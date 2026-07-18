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

  it("uses theme tokens for every color, not hardcoded hex tuned for a white background", () => {
    const html = renderStopPopupHTML(
      {
        stop_name: "Themed",
        stop_code: "SC1",
        platform_code: "2",
        stop_id: "1_01",
        avg_min: 3.0,
        samples: 10,
        contributing_routes: ["R1"],
      },
      period,
      t,
    );
    // None of the old hardcoded, white-background-only colors remain.
    for (const oldColor of ["#888", "#666", "#555", "#5b6cad", "#eef0fa"]) {
      expect(html).not.toContain(oldColor);
    }
    // Every text color is a theme token instead.
    expect(html).toContain("var(--text-secondary)");
    expect(html).toContain("var(--text-tertiary)");
    expect(html).toContain("var(--accent)");
    expect(html).toContain("var(--accent-soft)");
  });
});
