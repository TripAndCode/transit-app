import type { AgencyRun } from "../../api/admin";

/** How a run reads as a whole: still going, finished cleanly, or stopped
 *  (a failed step, or a run another job turned away). */
export type RunState = "running" | "done" | "stopped";

export function runState(run: AgencyRun): RunState {
  if (run.status === "running") return "running";
  return run.status === "ok" ? "done" : "stopped";
}

/** The step the run is on: the one running, or -- between two steps, while
 *  the next has not opened its row yet -- the first one still to come. -1
 *  once the run has ended. */
export function currentStepIndex(run: AgencyRun): number {
  if (run.status !== "running") return -1;
  const running = run.steps.findIndex((s) => s.status === "running");
  return running !== -1 ? running : run.steps.findIndex((s) => s.status === "pending");
}
