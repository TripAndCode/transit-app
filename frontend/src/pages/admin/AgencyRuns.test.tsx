import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { AgencyHealthRow, AgencyRun, AgencyRunStep } from "../../api/admin";
import { formatShortDate } from "../../utils/format";
import { AgencyRunCard, DataStage, RunChain, TimetableStage } from "./AgencyRuns";
import { currentStepIndex, runState, stoppedStepIndex } from "./agencyRunState";

function step(over: Partial<AgencyRunStep> & Pick<AgencyRunStep, "step" | "status">): AgencyRunStep {
  return { started_at: null, finished_at: null, rows: null, error: null, ...over };
}

function run(over: Partial<AgencyRun>): AgencyRun {
  return {
    run_id: 7,
    kind: "update",
    status: "running",
    started_at: "2026-10-13T05:00:00Z",
    finished_at: null,
    error: null,
    steps: [],
    ...over,
  };
}

const RUNNING = run({
  steps: [
    step({ step: "static", status: "ok", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:48Z", rows: 27 }),
    step({ step: "ingest", status: "running", started_at: "2026-10-13T05:00:48Z" }),
    step({ step: "analyze", status: "pending" }),
  ],
});

const DONE = run({
  status: "ok",
  finished_at: "2026-10-13T05:04:28Z",
  steps: [
    step({ step: "static", status: "ok", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:52Z", rows: 166 }),
    step({ step: "ingest", status: "ok", started_at: "2026-10-13T05:00:52Z", finished_at: "2026-10-13T05:02:57Z" }),
    step({ step: "analyze", status: "ok", started_at: "2026-10-13T05:02:57Z", finished_at: "2026-10-13T05:04:28Z" }),
  ],
});

const FAILED = run({
  status: "error",
  finished_at: "2026-10-13T05:00:10Z",
  error: "StaticFetchError: HTTP 404 Not Found",
  steps: [
    step({
      step: "static",
      status: "error",
      started_at: "2026-10-13T05:00:00Z",
      finished_at: "2026-10-13T05:00:10Z",
      error: "StaticFetchError: HTTP 404 Not Found from https://example.test/gtfs/current_data.zip",
    }),
    step({ step: "ingest", status: "not_run" }),
    step({ step: "analyze", status: "not_run" }),
  ],
});

function wrap(ui: React.ReactElement) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>{ui}</MemoryRouter>
    </I18nextProvider>,
  );
}

function card(
  r: AgencyRun,
  handlers: { onRetry?: () => void; onEditUrl?: () => void } = {},
  inFlight = r.status === "running",
) {
  return wrap(
    <AgencyRunCard
      agencyId={9}
      agencyName="Hiroshima Bus"
      run={r}
      inFlight={inFlight}
      latestDataDate="2026-10-12"
      retryBlocked={false}
      onRetry={handlers.onRetry ?? vi.fn()}
      onEditUrl={handlers.onEditUrl ?? vi.fn()}
    />,
  );
}

function stepRows() {
  return within(screen.getByRole("region", { name: "Latest run" }))
    .getAllByRole("listitem")
    .map((li) => li.className.replace("agency-run-step agency-run-step--", ""));
}

describe("run state helpers", () => {
  it("is on the running step, or between steps on the next one to start", () => {
    expect(currentStepIndex(RUNNING)).toBe(1);
    const between = run({
      steps: [step({ step: "static", status: "ok" }), step({ step: "ingest", status: "pending" })],
    });
    expect(currentStepIndex(between)).toBe(1);
    expect(currentStepIndex(DONE)).toBe(-1);
  });

  it("reads an ok or unchanged run as done and anything else that ended as stopped", () => {
    expect(runState(RUNNING, true)).toBe("running");
    expect(runState(DONE, false)).toBe("done");
    expect(runState(run({ status: "unchanged" }), false)).toBe("done");
    expect(runState(FAILED, false)).toBe("stopped");
    expect(runState(run({ status: "skipped" }), false)).toBe("stopped");
  });

  it("reads a run row still open but no longer in flight as stopped", () => {
    expect(runState(RUNNING, false)).toBe("stopped");
  });

  it("finds where a run stopped past a step that found nothing newer", () => {
    const stopped = run({
      status: "error",
      steps: [
        step({ step: "static", status: "unchanged" }),
        step({ step: "ingest", status: "error" }),
        step({ step: "analyze", status: "not_run" }),
      ],
    });
    expect(stoppedStepIndex(stopped)).toBe(1);
  });
});

describe("AgencyRunCard", () => {
  it("shows finished steps with their result, the current one elapsing, and the rest waiting", () => {
    card(RUNNING);
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Step 2 of 3 running" })).toBeTruthy();
    expect(stepRows()).toEqual(["done", "now", "pending"]);
    expect(screen.getByText("27 routes · 48 s")).toBeTruthy();
    expect(screen.getByText(/elapsed$/)).toBeTruthy();
    expect(screen.getByText("Waiting")).toBeTruthy();
    expect(screen.getByText(/keeps going if you leave the page/)).toBeTruthy();
  });

  it("draws the current segment striped and the others by their state", () => {
    const { container } = card(RUNNING);
    const segs = [...container.querySelectorAll(".agency-run-seg")].map((el) => el.className);
    expect(segs).toEqual(["agency-run-seg agency-run-seg--done", "agency-run-seg agency-run-seg--now", "agency-run-seg"]);
  });

  it("ends a finished run with its total time and a way to the agency's Pulse page", () => {
    card(DONE);
    expect(screen.getByText("Done")).toBeTruthy();
    expect(screen.getByRole("img", { name: "All 3 steps done" })).toBeTruthy();
    expect(stepRows()).toEqual(["done", "done", "done"]);
    expect(screen.getByText(`Through ${formatShortDate("2026-10-12")} · 1 min 31 s`)).toBeTruthy();
    expect(screen.getByText(/4 min 28 s in total/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Pulse ›" }).getAttribute("href")).toBe("/agencies/9/pulse");
  });

  it("says which step stopped the run and why, leaves the later steps unrun, and offers retry and the URL", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const onEditUrl = vi.fn();
    card(FAILED, { onRetry, onEditUrl });
    expect(screen.getByText("Stopped")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Stopped at step 1 of 3" })).toBeTruthy();
    expect(stepRows()).toEqual(["fail", "not_run", "not_run"]);
    expect(screen.getByText("Timetable could not be fetched")).toBeTruthy();
    expect(screen.getByText(/HTTP 404 Not Found from https:\/\/example\.test\/gtfs\/current_data\.zip/)).toBeTruthy();
    expect(screen.getAllByText("Not run")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Edit URL" }));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onEditUrl).toHaveBeenCalledOnce();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("shows a timetable check that found nothing newer as done, saying so", () => {
    card(
      run({
        ...DONE,
        steps: [
          step({ step: "static", status: "unchanged", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:03Z" }),
          ...DONE.steps.slice(1),
        ],
      }),
    );
    expect(stepRows()).toEqual(["done", "done", "done"]);
    expect(screen.getByText("No newer timetable found · 3 s")).toBeTruthy();
  });

  it("says the later steps did not run when the run stopped at the failure", () => {
    card(
      run({
        kind: "update",
        status: "error",
        finished_at: "2026-10-13T05:02:00Z",
        steps: [
          step({ step: "static", status: "ok", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:40Z" }),
          step({ step: "ingest", status: "error", started_at: "2026-10-13T05:00:40Z", finished_at: "2026-10-13T05:02:00Z", error: "RuntimeError: feed down" }),
          step({ step: "analyze", status: "not_run" }),
        ],
      }),
    );
    expect(screen.getByText(/the steps after it did not run/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit URL" })).toBeNull();
  });

  it("says the run went on when a re-aggregation kept going past a failed ingest", () => {
    card(
      run({
        kind: "ingest",
        status: "error",
        finished_at: "2026-10-13T05:03:00Z",
        steps: [
          step({ step: "ingest", status: "error", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:01:00Z", error: "RuntimeError: feed down" }),
          step({ step: "analyze", status: "ok", started_at: "2026-10-13T05:01:00Z", finished_at: "2026-10-13T05:03:00Z" }),
        ],
      }),
    );
    expect(stepRows()).toEqual(["fail", "done"]);
    expect(screen.getByText(/still went on to the steps after it/)).toBeTruthy();
    expect(screen.queryByText(/did not run/)).toBeNull();
  });

  it("names the step a run stopped at even after an unchanged timetable check", () => {
    card(
      run({
        status: "error",
        finished_at: "2026-10-13T05:02:00Z",
        steps: [
          step({ step: "static", status: "unchanged", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:03Z" }),
          step({ step: "ingest", status: "error", started_at: "2026-10-13T05:00:03Z", finished_at: "2026-10-13T05:02:00Z", error: "RuntimeError: feed down" }),
          step({ step: "analyze", status: "not_run" }),
        ],
      }),
    );
    expect(screen.getByRole("img", { name: "Stopped at step 2 of 3" })).toBeTruthy();
  });

  it("shows a run whose process died as stopped where it was, with a retry and no ticking timer", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    card(RUNNING, { onRetry }, false);
    expect(screen.getByText("Stopped")).toBeTruthy();
    expect(screen.queryByText(/elapsed$/)).toBeNull();
    expect(stepRows()).toEqual(["done", "fail", "not_run"]);
    expect(screen.getByText("Did not finish")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Stopped at step 2 of 3" })).toBeTruthy();
    expect(screen.getByText(/stopped reporting before it finished/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("reads a reload on its own that found nothing newer as done", () => {
    card(
      run({
        kind: "static",
        status: "unchanged",
        finished_at: "2026-10-13T05:00:03Z",
        steps: [step({ step: "static", status: "unchanged", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:03Z" })],
      }),
    );
    expect(screen.getByText("Done")).toBeTruthy();
    expect(screen.getByRole("img", { name: "All 1 steps done" })).toBeTruthy();
  });

  it("explains a run another job turned away, without blaming the timetable URL", () => {
    card(
      run({
        status: "skipped",
        finished_at: "2026-10-13T05:01:00Z",
        steps: [
          step({ step: "static", status: "ok", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:40Z" }),
          step({ step: "ingest", status: "not_run" }),
          step({ step: "analyze", status: "not_run" }),
        ],
      }),
    );
    expect(screen.getByText(/usually the scheduled one/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit URL" })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

describe("RunChain", () => {
  it("lists every step with the current one marked, and when the run started", () => {
    const { container } = wrap(<RunChain run={RUNNING} />);
    const pills = [...container.querySelectorAll(".agency-chain-step")].map((el) => [el.textContent, el.className]);
    expect(pills).toEqual([
      ["Timetable", "agency-chain-step agency-chain-step--done"],
      ["Ingest", "agency-chain-step agency-chain-step--now"],
      ["Aggregate", "agency-chain-step agency-chain-step--pending"],
    ]);
    expect(screen.getByText(/^Started .* · Ingest…$/)).toBeTruthy();
  });
});

describe("stage cells", () => {
  const health = {
    static_loaded_on: "2026-09-18",
    static_version: { version: "gtfs_static_20260918", analyzed_at: null },
    latest_data_date: "2026-09-16",
    freshness: "stale",
  } as AgencyHealthRow;

  it("shows the timetable's load day and reloads it", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    const { container } = wrap(<TimetableStage health={health} blocker={null} onRun={onRun} />);
    expect(container.querySelector(".agency-stage--done")?.textContent).toMatch(/^Loaded /);
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("names the loaded version when no load day is known, and says so when nothing is loaded", () => {
    const { unmount } = wrap(
      <TimetableStage health={{ ...health, static_loaded_on: null }} blocker={null} onRun={vi.fn()} />,
    );
    expect(screen.getByText("gtfs_static_20260918")).toBeTruthy();
    unmount();
    wrap(<TimetableStage health={{ ...health, static_loaded_on: null, static_version: null }} blocker={null} onRun={vi.fn()} />);
    expect(screen.getByText("Not loaded")).toBeTruthy();
  });

  it("tones the data stage by freshness and keeps the freshness word for screen readers", () => {
    const { container } = wrap(<DataStage health={health} blocker={null} onRun={vi.fn()} />);
    expect(container.querySelector(".agency-stage--warn")?.textContent).toMatch(/^Through .*Behind$/);
  });

  it("disables an action that cannot run and says why", () => {
    wrap(<DataStage health={health} blocker="A run for this agency is in progress." onRun={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Re-aggregate" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("A run for this agency is in progress.");
  });
});
