import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ModelPage } from "./ModelPage";
import * as hooks from "../api/hooks";
import { clearLastAgency, writeLastAgency } from "../api/lastAgency";
import i18n from "../i18n";
import type { Agency, MlEvaluations } from "../api/types";

function evaluations(partial: Partial<MlEvaluations> = {}): MlEvaluations {
  return {
    latest: {
      run_date: "2026-10-25",
      adopted: true,
      failures: [],
      skill: 0.051,
      coverage: 0.809,
      interval_coverage: 0.809,
      thresholds: { min_skill: 0.05, coverage_low: 0.75, coverage_high: 0.85, min_model_days: 56 },
      point_metric: "mean",
      serving_model_version: "2026-10-24",
      origin_skill: { count: 28, min: -0.0182, median: 0.0444, max: 0.134 },
      agencies: [
        { agency_id: 8, agency_name: "Hiroden", model_status: "model", skill: 0.066, lgbm_mae: 1.044, b0_mae: 1.101, days_of_data: 140, forecast_live: true },
        { agency_id: 54, agency_name: "Asahi", model_status: "b0", skill: null, lgbm_mae: 1.25, b0_mae: 1.2, days_of_data: 20, forecast_live: false },
      ],
    },
    history: [
      { run_date: "2026-10-25", adopted: true, overall_skill: 0.051, point_metric: "q50" },
      { run_date: "2026-10-18", adopted: true, overall_skill: 0.063, point_metric: "mean" },
    ],
    forecast_in_use: true,
    ...partial,
  };
}

function withLatest(patch: Partial<NonNullable<MlEvaluations["latest"]>>): MlEvaluations {
  const data = evaluations();
  data.latest = { ...data.latest!, ...patch };
  return data;
}

const AGENCIES: Partial<Agency>[] = [
  { agency_id: 1, agency_name: "Aomori" },
  { agency_id: 8, agency_name: "Hiroden" },
];

function renderPage(data: MlEvaluations | undefined, path = "/model") {
  vi.spyOn(hooks, "useMlEvaluations").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: AGENCIES } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <ModelPage />
    </MemoryRouter>,
  );
}

function onPhone() {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(max-width: 640px)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

afterEach(() => {
  vi.restoreAllMocks();
  clearLastAgency();
});

describe("ModelPage", () => {
  it("shows an adopted week's verdict, skill and the model in use", () => {
    renderPage(evaluations());
    expect(screen.getByTestId("model-verdict")).toHaveTextContent("Adopted");
    expect(screen.getByTestId("model-skill")).toHaveTextContent("5.1% less error");
    expect(screen.getByText("Model in use: 2026-10-24")).toBeInTheDocument();
  });

  it("words a not-adopted week's reasons in the reader's language, and survives an unknown code", () => {
    renderPage(
      withLatest({
        adopted: false,
        failures: [
          { code: "skill_below_min", value: 0.046, agencies: [] },
          { code: "something_new", value: null, agencies: [] },
        ],
      }),
    );
    expect(screen.getByTestId("model-verdict")).toHaveTextContent("Not adopted");
    const reasons = screen.getByTestId("model-failures");
    expect(reasons).toHaveTextContent("Error improved by only 4.6% against the usual estimate, short of the 5.0% bar");
    expect(reasons).toHaveTextContent("Another check did not pass (something_new)");
  });

  it("shows neither a skill nor a model error for an agency on the usual estimate", () => {
    renderPage(evaluations());
    const row = within(screen.getByTestId("model-agencies")).getByText("Asahi").closest("tr")!;
    expect(row).toHaveTextContent("On the usual estimate (too little history)");
    expect(row).toHaveTextContent("—");
    expect(row).not.toHaveTextContent("0.0%");
    expect(row).toHaveTextContent("usual estimate 72 s");
    expect(row).not.toHaveTextContent("model 75 s");
  });

  it("flags the week the scoring metric changed", () => {
    renderPage(evaluations());
    const history = screen.getByTestId("model-history");
    expect(history).toHaveTextContent("From this week on, scored with the median");
  });

  it("says when the ML forecast is not in the route forecast", () => {
    renderPage(evaluations({ forecast_in_use: false }));
    expect(
      screen.getByText("ML forecasts are not in the route forecast right now: the ML forecast flag is off in admin → Flags."),
    ).toBeInTheDocument();
  });

  it("marks an agency whose forecast the route forecast shows now", () => {
    renderPage(evaluations());
    const row = within(screen.getByTestId("model-agencies")).getByText("Hiroden").closest("tr")!;
    expect(row).toHaveTextContent("In the route forecast now");
  });

  it("shows the coverage the adoption gate judged, and the published slot range apart from it", () => {
    renderPage(withLatest({ coverage: 0.74, interval_coverage: 0.781 }));
    const card = screen.getByTestId("model-coverage");
    expect(card).toHaveTextContent("74.0%");
    expect(card).toHaveTextContent("Per time slot, as published: 78.1%");
  });

  it("shows a calm empty state before the first evaluation", () => {
    renderPage(evaluations({ latest: null, history: [] }));
    expect(screen.getByText("No evaluation yet. It is updated every Sunday.")).toBeInTheDocument();
  });
});

describe("ModelPage mean error against the skill", () => {
  it("labels the mean errors as every-slot figures, in seconds, apart from the both-forecast skill", () => {
    renderPage(evaluations());
    const table = screen.getByTestId("model-agencies");
    expect(within(table).getByRole("columnheader", { name: "Mean error (every slot)" })).toBeInTheDocument();
    const row = within(table).getByText("Hiroden").closest("tr")!;
    expect(row).toHaveTextContent("6.6% less error");
    expect(row).toHaveTextContent("model 63 s / usual estimate 66 s");
    expect(screen.getByTestId("model-mae-note")).toHaveTextContent(
      "“Against the usual estimate” compares error only on the time slots (route × day × hour) both could forecast. " +
        "Each mean error covers every slot that method could forecast, and the usual estimate cannot forecast a slot " +
        "with no record in the past 4 weeks, so the gap between the two can differ from that comparison.",
    );
  });
});

describe("ModelPage headline against the bar", () => {
  it("says by how many points the skill cleared the bar", () => {
    renderPage(evaluations());
    expect(screen.getByTestId("model-skill")).toHaveTextContent("0.1 points above the bar");
  });

  it("says by how many points the skill fell short of the bar", () => {
    renderPage(withLatest({ skill: 0.046 }));
    expect(screen.getByTestId("model-skill")).toHaveTextContent("0.4 points short of the bar");
  });

  it("says only just, rather than 0.0 points, when the margin rounds away", () => {
    renderPage(withLatest({ skill: 0.0502 }));
    expect(screen.getByTestId("model-skill")).toHaveTextContent("Only just reached the bar");
  });

  it("says only just short when a failing skill rounds to the bar", () => {
    renderPage(withLatest({ skill: 0.0498 }));
    expect(screen.getByTestId("model-skill")).toHaveTextContent("Only just short of the bar");
  });

  it("states no margin without a bar", () => {
    renderPage(withLatest({ thresholds: { min_skill: null, coverage_low: 0.75, coverage_high: 0.85, min_model_days: 56 } }));
    expect(screen.getByTestId("model-skill")).not.toHaveTextContent(/above the bar|short of the bar|reached the bar/);
  });

  it("shows how the skill spread across test days, and what a negative day means", () => {
    renderPage(evaluations());
    const card = screen.getByTestId("model-skill");
    expect(card).toHaveTextContent("By test day: -1.8% to 13.4% (median 4.4%, 28 days)");
    expect(card).toHaveTextContent("Below zero: days the usual estimate was more accurate");
  });

  it("leaves out the negative-day note when no day was worse", () => {
    renderPage(withLatest({ origin_skill: { count: 28, min: 0.01, median: 0.044, max: 0.134 } }));
    const card = screen.getByTestId("model-skill");
    expect(card).toHaveTextContent("By test day: 1.0% to 13.4% (median 4.4%, 28 days)");
    expect(card).not.toHaveTextContent("Below zero");
  });

  it("leaves out the spread for a summary that carries none", () => {
    renderPage(withLatest({ origin_skill: { count: 0, min: null, median: null, max: null } }));
    expect(screen.getByTestId("model-skill")).not.toHaveTextContent("By test day");
  });
});

describe("ModelPage on a phone", () => {
  it("lists each agency as one card instead of a four-column table", () => {
    onPhone();
    renderPage(evaluations());
    const block = screen.getByTestId("model-agencies");
    expect(block.querySelector("table")).toBeNull();
    const cards = within(block).getAllByRole("listitem");
    expect(cards).toHaveLength(2);
    expect(cards.every((c) => c.classList.contains("model-card"))).toBe(true);
    const hiroden = cards[0];
    expect(hiroden).toHaveTextContent("Hiroden");
    expect(hiroden).toHaveTextContent("In the route forecast now");
    expect(hiroden).toHaveTextContent("6.6% less error");
    expect(hiroden).toHaveTextContent("On the model");
    expect(hiroden).toHaveTextContent("model 63 s / usual estimate 66 s");
    expect(cards[1]).toHaveTextContent("On the usual estimate (too little history)");
    expect(cards[1]).toHaveTextContent("usual estimate 72 s");
    expect(screen.getByTestId("model-mae-note")).toBeInTheDocument();
  });

  it("lists each past week as one card, keeping the metric-change note", () => {
    onPhone();
    renderPage(evaluations());
    const history = screen.getByTestId("model-history");
    expect(history.tagName).toBe("UL");
    const weeks = within(history).getAllByRole("listitem");
    expect(weeks).toHaveLength(2);
    expect(weeks[0]).toHaveTextContent("2026-10-25");
    expect(weeks[0]).toHaveTextContent("Adopted");
    expect(weeks[0]).toHaveTextContent("5.1% less error");
    expect(weeks[0]).toHaveTextContent("From this week on, scored with the median");
    expect(weeks[1]).not.toHaveTextContent("From this week on");
  });
});

describe("ModelPage way back to an agency", () => {
  it("links back to the agency the visitor came from", () => {
    writeLastAgency(1);
    renderPage(evaluations(), "/model?from=8");
    expect(screen.getByRole("link", { name: "← Back to Hiroden" })).toHaveAttribute("href", "/agencies/8/pulse");
  });

  it("falls back to the last chosen agency on a direct visit", () => {
    writeLastAgency(1);
    renderPage(evaluations());
    expect(screen.getByRole("link", { name: "← Back to Aomori" })).toHaveAttribute("href", "/agencies/1/pulse");
  });

  it("offers no way back to an agency it does not know", () => {
    renderPage(evaluations(), "/model?from=999");
    expect(screen.queryByRole("link", { name: /Back to/ })).toBeNull();
  });
});

describe("ModelPage in Japanese", () => {
  beforeAll(async () => await i18n.changeLanguage("ja"));
  afterAll(async () => await i18n.changeLanguage("en"));

  it("names the baseline and the operators in plain words, never B0 or エージェンシー", () => {
    renderPage(evaluations());
    expect(
      screen.getByText("毎週の検証で、モデルの予測が従来の見込み（過去4週の同じ曜日・同じ時間帯の平均遅延）よりどれだけ正確かを確かめています。"),
    ).toBeInTheDocument();
    expect(screen.getByText("事業者別")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "事業者" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "平均誤差（全時間枠）" })).toBeInTheDocument();
    const row = within(screen.getByTestId("model-agencies")).getByText("Hiroden").closest("tr")!;
    expect(row).toHaveTextContent("路線別予測に反映中");
    expect(row).toHaveTextContent("モデル 63秒 / 従来の見込み 66秒");
    expect(document.body).not.toHaveTextContent("B0");
    expect(document.body).not.toHaveTextContent("エージェンシー");
    expect(document.body).not.toHaveTextContent("ヒートマップ");
  });

  it("says the margin to the bar and the spread across test days", () => {
    renderPage(evaluations());
    const card = screen.getByTestId("model-skill");
    expect(card).toHaveTextContent("基準を0.1ポイント上回った");
    expect(card).toHaveTextContent("検証日ごとの改善: -1.8%〜13.4%（中央値4.4%、28日）");
    expect(card).toHaveTextContent("マイナスは従来の見込みのほうが正確だった日");
  });

  it("explains that the comparison and the mean errors cover different slots", () => {
    renderPage(evaluations());
    expect(screen.getByTestId("model-mae-note")).toHaveTextContent(
      "「従来の見込みとの比較」は、両方が予測できた時間枠（系統×日×1時間）だけで誤差を比べています。" +
        "平均誤差はそれぞれが予測できたすべての時間枠での値で、従来の見込みは過去4週に記録のない時間枠を予測できないため、" +
        "2つの差はこの比較と一致しないことがあります。",
    );
  });

  it("links back to the agency in Japanese", () => {
    renderPage(evaluations(), "/model?from=8");
    expect(screen.getByRole("link", { name: "← Hirodenに戻る" })).toHaveAttribute("href", "/agencies/8/pulse");
  });
});
