import type { AgencyRun } from "../../api/admin";

/** How a run reads as a whole: still going, finished cleanly, or stopped
 *  (a failed step, a run another job turned away, or one whose process died
 *  before it could close its row). */
export type RunState = "running" | "done" | "stopped";

/** `inFlight` is the server's own rule for work in progress, the one a new
 *  press is refused by. A row still `running` that the server no longer
 *  counts was left by a process that died, and reads as stopped. */
export function runState(run: AgencyRun, inFlight: boolean): RunState {
  if (run.status === "running") return inFlight ? "running" : "stopped";
  return run.status === "ok" || run.status === "unchanged" ? "done" : "stopped";
}

/** The run's process died before it closed its row. */
export function isAbandoned(run: AgencyRun, inFlight: boolean): boolean {
  return run.status === "running" && !inFlight;
}

/** The step a still-open run is on: the one running, or -- between two
 *  steps, while the next has not opened its row yet -- the first one still
 *  to come. -1 once the run's row has closed. */
export function currentStepIndex(run: AgencyRun): number {
  if (run.status !== "running") return -1;
  const running = run.steps.findIndex((s) => s.status === "running");
  return running !== -1 ? running : run.steps.findIndex((s) => s.status === "pending");
}

/** Where a stopped run stopped: its failed or turned-away step, else the
 *  first step that did not finish, else its last. */
export function stoppedStepIndex(run: AgencyRun): number {
  const failed = run.steps.findIndex((s) => s.status === "error" || s.status === "skipped");
  if (failed !== -1) return failed;
  const unfinished = run.steps.findIndex((s) => s.status !== "ok" && s.status !== "unchanged");
  return unfinished !== -1 ? unfinished : run.steps.length - 1;
}
