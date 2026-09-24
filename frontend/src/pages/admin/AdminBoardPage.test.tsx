import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { AdminBoard, PipelineRun } from "../../api/admin";
import { AdminBoardPage } from "./AdminBoardPage";

function days(states: ("fresh" | "stale" | "missing")[], clampOnLast = 0.2) {
  return states.map((state, i) => ({
    date: `2026-09-${String(6 + i).padStart(2, "0")}`,
    state,
    clamp_pct: state === "missing" ? null : i === states.length - 1 ? clampOnLast : 0.1,
  }));
}

const BOARD: AdminBoard = {
  collectors: [
    {
      key: "oracle_crawler",
      label: "Oracle crawler",
      status: "ok",
      last_success_at: "2026-09-20T08:10:00Z",
      detail: null,
      history: Array(24).fill(1),
    },
    {
      key: "r2",
      label: "R2 sync",
      status: "warn",
      last_success_at: "2026-09-20T04:10:00Z",
      detail: "disk usage is degraded",
      history: [...Array(20).fill(1), 0, 0, 0, 0],
    },
    { key: "vps_loop", label: "VPS loop", status: "down", last_success_at: null, detail: null, history: Array(24).fill(0) },
    { key: "github", label: "CI (GitHub)", status: "unknown", last_success_at: null, detail: null, history: Array(24).fill(0) },
  ],
  freshness: [
    { agency_id: 1, agency_name: "Hokuriku", days: days(Array(14).fill("fresh")) },
    {
      agency_id: 2,
      agency_name: "Toyama Bayline",
      days: days([...Array(11).fill("fresh"), "stale", "missing", "missing"]),
    },
  ],
  migrations: { applied: "0053", latest: "0053", behind: 0 },
  runs: [
    {
      run_id: 1,
      kind: "ingest",
      agency_id: null,
      agency_name: null,
      started_at: "2026-09-20T19:00:00Z",
      finished_at: "2026-09-20T19:20:00Z",
      status: "ok",
      rows: 1200,
      lock_wait_ms: null,
      error: null,
      requested_by: null,
    },
    {
      run_id: 2,
      kind: "analyze",
      agency_id: 2,
      agency_name: "Toyama Bayline",
      started_at: "2026-09-20T19:20:00Z",
      finished_at: null,
      status: "skipped",
      rows: null,
      lock_wait_ms: 3,
      error: null,
      requested_by: null,
    },
  ] as PipelineRun[],
  alerts: [
    {
      level: "warn",
      code: "agency_stale",
      params: { agency: "Toyama Bayline", days: 3 },
      text: "Toyama Bayline: aggregates 3 day(s) behind",
      href: "/admin/ops",
    },
    {
      level: "info",
      code: "llm_approvals_pending",
      params: { count: 2 },
      text: "2 user(s) awaiting AI access approval",
      href: "/admin/users",
    },
  ],
};

let mockQuery: { data?: AdminBoard; error: unknown; isPending: boolean };
let mockTrigger: { mutate: ReturnType<typeof vi.fn>; isPending: boolean; isSuccess: boolean; error: unknown };

vi.mock("../../api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/admin")>()),
  useAdminBoard: () => mockQuery,
  useTriggerRun: () => mockTrigger,
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe("AdminBoardPage", () => {
  beforeEach(() => {
    mockQuery = { data: BOARD, error: null, isPending: false };
    mockTrigger = { mutate: vi.fn(), isPending: false, isSuccess: false, error: null };
    // The timeline is anchored to the current JST day, so the fixture's runs
    // only land on the axis with the clock pinned inside that day.
    // `shouldAdvanceTime` keeps react-query and user-event's own waiting
    // working while the wall clock is pinned.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-09-20T23:12:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders one tile per collector with its status and last success", () => {
    wrap(<AdminBoardPage />);
    const tiles = screen.getAllByTestId("collector-tile");
    expect(tiles).toHaveLength(4);
    expect(within(tiles[0]).getByText(i18n.t("admin.board.collector.oracle_crawler"))).toBeInTheDocument();
    expect(within(tiles[0]).getByText(i18n.t("admin.board.status.ok"))).toBeInTheDocument();
    expect(within(tiles[2]).getByText(i18n.t("admin.board.never"))).toBeInTheDocument();
  });

  it("gives every collector sparkline an accessible summary instead of bare shapes", () => {
    wrap(<AdminBoardPage />);
    const sparkline = screen.getAllByTestId("collector-sparkline")[1];
    expect(sparkline).toHaveAttribute("role", "img");
    expect(sparkline.getAttribute("aria-label")).toContain("20");
  });

  it("renders the freshness heatmap as one labelled row per agency x 14 days", () => {
    wrap(<AdminBoardPage />);
    const rows = screen.getAllByTestId("freshness-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText("Toyama Bayline")).toBeInTheDocument();
    expect(within(rows[1]).getAllByTestId("freshness-cell")).toHaveLength(14);
  });

  it("distinguishes the three cell states and puts the day's detail in its tooltip", () => {
    wrap(<AdminBoardPage />);
    const cells = within(screen.getAllByTestId("freshness-row")[1]).getAllByTestId("freshness-cell");
    expect(cells[10]).toHaveAttribute("data-state", "fresh");
    expect(cells[11]).toHaveAttribute("data-state", "stale");
    expect(cells[13]).toHaveAttribute("data-state", "missing");
    expect(cells[11].getAttribute("title")).toContain("2026-09-17");
    expect(cells[11].getAttribute("title")).toContain(i18n.t("admin.board.state.stale"));
  });

  it("translates each alert and links it into the page that fixes it", () => {
    wrap(<AdminBoardPage />);
    const items = screen.getAllByTestId("board-alert");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText(/Toyama Bayline/)).toBeInTheDocument();
    expect(within(items[0]).getByText(/3/)).toBeInTheDocument();
    expect(within(items[0]).getByRole("link")).toHaveAttribute("href", "/admin/ops");
    // `count` is i18next's plural selector; a key with no plural forms must
    // still interpolate it rather than falling through to an empty string.
    expect(within(items[1]).getByText(/2/)).toBeInTheDocument();
    expect(within(items[1]).getByRole("link")).toHaveAttribute("href", "/admin/users");
  });

  it("renders the untranslated server text for an alert code it does not know", () => {
    mockQuery = {
      data: {
        ...BOARD,
        alerts: [{ level: "warn", code: "invented_later", params: {}, text: "Something new happened", href: null }],
      },
      error: null,
      isPending: false,
    };
    wrap(<AdminBoardPage />);
    expect(screen.getByText("Something new happened")).toBeInTheDocument();
  });

  it("says so when there is nothing to act on", () => {
    mockQuery = { data: { ...BOARD, alerts: [] }, error: null, isPending: false };
    wrap(<AdminBoardPage />);
    expect(screen.getByText(i18n.t("admin.board.alerts_none"))).toBeInTheDocument();
  });

  it("draws one timeline lane per kind and agency, in pipeline order", () => {
    wrap(<AdminBoardPage />);
    const lanes = screen.getAllByTestId("run-lane");
    expect(lanes).toHaveLength(2);
    expect(lanes[0]).toHaveAttribute("data-lane", "ingest:all");
    expect(lanes[1]).toHaveAttribute("data-lane", "analyze:2");
  });

  it("marks a displaced run so it does not read as a fast successful one", () => {
    wrap(<AdminBoardPage />);
    const bars = screen.getAllByTestId("run-bar");
    expect(bars[0]).toHaveAttribute("data-dashed", "false");
    expect(bars[1]).toHaveAttribute("data-dashed", "true");
    expect(bars[1]).toHaveAttribute("data-status", "skipped");
  });

  it("puts the run's own detail in its tooltip rather than relying on the bar's colour", () => {
    wrap(<AdminBoardPage />);
    const title = screen.getAllByTestId("run-bar")[1].querySelector("title")?.textContent ?? "";
    expect(title).toContain(i18n.t("admin.board.run_status.skipped"));
    expect(title).toContain(i18n.t("admin.board.run_tooltip_lock"));
  });

  it("marks where now falls on the day's axis", () => {
    wrap(<AdminBoardPage />);
    expect(screen.getByTestId("run-now-marker")).toBeInTheDocument();
    expect(screen.getByText(i18n.t("admin.board.runs_now"))).toBeInTheDocument();
  });

  it("says so when nothing has run today instead of drawing an empty chart", () => {
    mockQuery = { data: { ...BOARD, runs: [] }, error: null, isPending: false };
    wrap(<AdminBoardPage />);
    expect(screen.getByText(i18n.t("admin.board.runs_empty"))).toBeInTheDocument();
    expect(screen.queryByTestId("run-timeline")).not.toBeInTheDocument();
  });

  it("asks before re-aggregating, and only triggers once confirmed", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    wrap(<AdminBoardPage />);
    const button = screen.getByRole("button", { name: i18n.t("admin.board.reanalyze") });
    expect(button).toBeEnabled();

    await user.click(button);
    expect(mockTrigger.mutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: i18n.t("admin.board.reanalyze_confirm_title") });

    await user.click(within(dialog).getByRole("button", { name: i18n.t("admin.board.reanalyze_confirm") }));
    expect(mockTrigger.mutate).toHaveBeenCalledWith({ kind: "ingest" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("leaves the pipeline alone when the confirmation is declined", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    wrap(<AdminBoardPage />);
    await user.click(screen.getByRole("button", { name: i18n.t("admin.board.reanalyze") }));
    await user.click(screen.getByRole("button", { name: i18n.t("admin.board.reanalyze_cancel") }));
    expect(mockTrigger.mutate).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks inside a modal dialog Escape can close, handing focus back to the trigger", async () => {
    // Re-aggregating is irreversible from here; the confirm has to behave
    // like every other dialog in the app (trapped, labelled, Escape-able)
    // rather than as a block of page content the operator can tab past.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    wrap(<AdminBoardPage />);
    const trigger = screen.getByRole("button", { name: i18n.t("admin.board.reanalyze") });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: i18n.t("admin.board.reanalyze_confirm_title") });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("button", { name: i18n.t("admin.board.reanalyze_confirm") })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockTrigger.mutate).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
  });

  it("reports a rejected trigger instead of leaving the operator guessing", () => {
    mockTrigger = { mutate: vi.fn(), isPending: false, isSuccess: false, error: new Error("nope") };
    wrap(<AdminBoardPage />);
    expect(screen.getByText(i18n.t("admin.board.reanalyze_error"))).toBeInTheDocument();
  });

  it("surfaces a load failure without blanking the page", () => {
    mockQuery = { data: undefined, error: new Error("boom"), isPending: false };
    wrap(<AdminBoardPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("admin.board.load_error"));
    expect(screen.getByRole("heading", { name: i18n.t("admin.board.title") })).toBeInTheDocument();
  });

  it("does not claim an empty heatmap while the first fetch is still in flight", () => {
    mockQuery = { data: undefined, error: null, isPending: true };
    wrap(<AdminBoardPage />);
    expect(screen.queryByText(i18n.t("admin.board.freshness_empty"))).not.toBeInTheDocument();
  });
});
