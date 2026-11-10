import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { HeadlineReference } from "./HeadlineReference";
import type { OverviewHeadline, OverviewHeadlineReference } from "../../api/types";
import { formatDateRange } from "../../utils/format";

const RANGE = formatDateRange("2026-02-17", "2026-05-17", { year: false });

function reference(partial: Partial<OverviewHeadlineReference> = {}): OverviewHeadlineReference {
  return {
    avg_min: 2.5,
    delta_min: 0.8,
    delta_pct: 32,
    samples: 4200,
    window_from: "2026-02-17",
    window_to: "2026-05-17",
    days: 90,
    days_selected: 90,
    days_collected: 90,
    min_days: 72,
    min_samples: 100,
    reason: null,
    ...partial,
  };
}

/** A 7-day headline at 3.3 min whose reference is `ref`. */
function headline(ref: OverviewHeadlineReference | undefined, partial: Partial<OverviewHeadline> = {}): OverviewHeadline {
  return {
    avg_min: 3.3,
    baseline_avg_min: 2.8,
    delta_min: 0.5,
    delta_pct: 17.9,
    samples: 500,
    window_from: "2026-05-18",
    window_to: "2026-05-24",
    reference: ref,
    ...partial,
  };
}

function renderReference(h: OverviewHeadline, hasBaseline = true) {
  return renderWithProviders(<HeadlineReference headline={h} hasBaseline={hasBaseline} />);
}

describe("HeadlineReference", () => {
  it("names the window it averages, with the figure and a judgement against it", () => {
    renderReference(headline(reference()));
    expect(screen.getByText(`Past 90 days (${RANGE}): average delay 2.5 min`)).toBeInTheDocument();
    expect(screen.getByText("Worse than the past 90 days")).toBeInTheDocument();
  });

  it("says about the same inside the tolerance, and better below it", () => {
    const { unmount } = renderReference(headline(reference({ avg_min: 3.25, delta_min: 0.05, delta_pct: 1.5 })));
    expect(screen.getByText("About the same as the past 90 days")).toBeInTheDocument();
    unmount();
    renderReference(headline(reference({ avg_min: 4.1, delta_min: -0.8, delta_pct: -19.5 })));
    expect(screen.getByText("Better than the past 90 days")).toBeInTheDocument();
  });

  it("says how many days had data when the feed missed some", () => {
    renderReference(headline(reference({ days_collected: 84 })));
    expect(
      screen.getByText(`Past 90 days (${RANGE}), on the 84 days with data: average delay 2.5 min`),
    ).toBeInTheDocument();
  });

  it("explains the window, the scope, the floors and the tolerance", async () => {
    renderReference(headline(reference({ days_selected: 64, min_days: 52 })));
    await userEvent.click(screen.getByRole("button", { name: "Hint" }));
    expect(screen.getByText("About the past 90 days")).toBeInTheDocument();
    expect(screen.getByText(/at least 52 of the 64 days/)).toBeInTheDocument();
    expect(screen.getByText(/at least 100 observations/)).toBeInTheDocument();
    expect(screen.getByText(/within 0\.1 min or 5%/)).toBeInTheDocument();
  });

  it("states why a reference is withheld instead of printing a figure", () => {
    const cases: [Partial<OverviewHeadlineReference>, string][] = [
      [{ reason: "no_data", samples: 0, days_collected: 0 }, `Past 90 days (${RANGE}): no observations, so there is no usual level to compare with.`],
      [{ reason: "insufficient_days", days_collected: 24 }, `Past 90 days (${RANGE}): data on only 24 days, short of the 72 needed for a usual level.`],
      [{ reason: "insufficient_samples", samples: 60 }, `Past 90 days (${RANGE}): only 60 observations, short of the 100 needed for a usual level.`],
      [{ reason: "not_aggregated" }, "With both a time of day and routes chosen, the past 90 days are not compared."],
    ];
    for (const [partial, text] of cases) {
      const { unmount } = renderReference(headline(reference({ avg_min: null, delta_min: null, delta_pct: null, ...partial })));
      expect(screen.getByText(text)).toBeInTheDocument();
      expect(screen.queryByText(/than the past 90 days|as the past 90 days/)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("makes no judgement when either average runs early", () => {
    renderReference(headline(reference({ avg_min: 0.4, delta_min: -0.7, delta_pct: null }), { avg_min: -0.3 }));
    expect(screen.getByText(`Past 90 days (${RANGE}): average delay 0.4 min`)).toBeInTheDocument();
    expect(screen.queryByText(/than the past 90 days|as the past 90 days/)).not.toBeInTheDocument();
  });

  it("shows the reference without a judgement when the period has no figure", () => {
    renderReference(headline(reference({ delta_min: null, delta_pct: null }), { avg_min: null }), false);
    expect(screen.getByText(`Past 90 days (${RANGE}): average delay 2.5 min`)).toBeInTheDocument();
    expect(screen.queryByText(/than the past 90 days|as the past 90 days/)).not.toBeInTheDocument();
  });

  it("leaves the comparison to the verdict when the window before is these same 90 days", () => {
    const ninety = headline(reference(), { window_from: "2026-05-18", window_to: "2026-08-15" });
    const { container, unmount } = renderReference(ninety);
    expect(container).toBeEmptyDOMElement();
    unmount();
    // Without a verdict, the reference is the only comparison left to show.
    renderReference(ninety, false);
    expect(screen.getByText(`Past 90 days (${RANGE}): average delay 2.5 min`)).toBeInTheDocument();
  });

  it("renders nothing for a payload without a reference", () => {
    const { container } = renderReference(headline(undefined));
    expect(container).toBeEmptyDOMElement();
  });
});
