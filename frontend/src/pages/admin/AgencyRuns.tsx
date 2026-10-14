import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { AgencyHealthRow, AgencyRun, AgencyRunStep } from "../../api/admin";
import { useNow } from "../../hooks/useNow";
import { destHref } from "../../routes/destinations";
import { EM_DASH, formatDateTime, formatDuration, formatShortDate } from "../../utils/format";
import { AdminButton } from "./adminControls";
import { currentStepIndex, runState, type RunState } from "./agencyRunState";
import "./agencyRuns.css";

/** How a step is drawn: finished, the one the run is on, stopped there,
 *  still to come, or skipped because the run ended first. */
type StepView = "done" | "now" | "fail" | "pending" | "not_run";

function stepView(step: AgencyRunStep, isCurrent: boolean): StepView {
  if (isCurrent) return "now";
  if (step.status === "ok" || step.status === "unchanged") return "done";
  if (step.status === "error" || step.status === "skipped") return "fail";
  if (step.status === "not_run") return "not_run";
  return "pending";
}

/** When the current step began: its own start, or -- before its row exists
 *  -- the end of the step before it, or the run's start. */
function currentSince(run: AgencyRun, index: number): string | null {
  const step = run.steps[index];
  if (step?.started_at) return step.started_at;
  return run.steps[index - 1]?.finished_at ?? run.started_at;
}

function secondsBetween(from: string | null, to: string | null): number | null {
  if (!from || !to) return null;
  const ms = new Date(to).getTime() - new Date(from).getTime();
  return Number.isFinite(ms) ? Math.max(0, ms / 1000) : null;
}

function clockTime(iso: string | null): string {
  return iso ? formatDateTime(iso, { timeStyle: "short" }) : EM_DASH;
}

/** Time since `since`, advancing every second: only the current step
 *  mounts one, so a finished card does not re-render on a timer. */
function Elapsed({ since }: { since: string | null }) {
  const { t } = useTranslation();
  const now = useNow(1_000);
  const seconds = since ? Math.max(0, (now.getTime() - new Date(since).getTime()) / 1000) : null;
  return <>{t("admin.agency_run.res_elapsed", { duration: formatDuration(seconds) })}</>;
}

// ── table cells ──────────────────────────────────────────────────────────

function Stage({ tone, children }: { tone: "done" | "warn" | "neutral"; children: ReactNode }) {
  return <span className={`agency-stage agency-stage--${tone}`}>{children}</span>;
}

type StageActionProps = {
  /** Why the action cannot run now, shown as its hint; null when it can. */
  blocker: string | null;
  onRun: () => void;
};

/** The timetable stage: the day the current timetable loaded, and its reload. */
export function TimetableStage({ health, blocker, onRun }: { health?: AgencyHealthRow } & StageActionProps) {
  const { t } = useTranslation();
  const loadedOn = health?.static_loaded_on;
  const version = health?.static_version?.version;
  return (
    <div className="agency-stage-cell">
      <Stage tone={loadedOn || version ? "done" : "neutral"}>
        {loadedOn
          ? t("admin.agency_run.stage_loaded", { date: formatShortDate(loadedOn) })
          : (version ?? t("admin.agency_run.stage_not_loaded"))}
      </Stage>
      <AdminButton
        variant="secondary"
        className="agency-stage-act"
        disabled={blocker != null}
        title={blocker ?? undefined}
        onClick={onRun}
      >
        {t("admin.agency_run.reload")}
      </AdminButton>
    </div>
  );
}

/** The aggregated-data stage: the last day with numbers, toned by
 *  freshness, and the existing re-aggregation. */
export function DataStage({ health, blocker, onRun }: { health?: AgencyHealthRow } & StageActionProps) {
  const { t } = useTranslation();
  const tone = health?.freshness === "fresh" ? "done" : health?.freshness === "stale" ? "warn" : "neutral";
  return (
    <div className="agency-stage-cell">
      <Stage tone={tone}>
        {health?.latest_data_date
          ? t("admin.agency_run.stage_data_through", { date: formatShortDate(health.latest_data_date) })
          : t("admin.agency_run.stage_no_data")}
        {health && <span className="agency-sr-only">{t(`admin.agencies.freshness_${health.freshness}`)}</span>}
      </Stage>
      <AdminButton
        variant="secondary"
        className="agency-stage-act"
        disabled={blocker != null}
        title={blocker ?? undefined}
        onClick={onRun}
      >
        {t("admin.agency_run.reaggregate")}
      </AdminButton>
    </div>
  );
}

/** A run in flight, compact enough for a table cell: each step as a pill,
 *  then when it started and what it is doing now. */
export function RunChain({ run }: { run: AgencyRun }) {
  const { t } = useTranslation();
  const current = currentStepIndex(run);
  const currentStep = run.steps[current];
  return (
    <div>
      <ol className="agency-chain" aria-label={t("admin.agency_run.chain_label")}>
        {run.steps.map((step, i) => (
          <li key={step.step} style={{ display: "contents" }}>
            {i > 0 && (
              <span className="agency-chain-arrow" aria-hidden="true">
                →
              </span>
            )}
            <span className={`agency-chain-step agency-chain-step--${stepView(step, i === current)}`}>
              {t(`admin.agency_run.step_short.${step.step}`)}
            </span>
          </li>
        ))}
      </ol>
      {currentStep && (
        <span className="agency-chain-sub">
          {t("admin.agency_run.chain_status", {
            time: clockTime(run.started_at),
            step: t(`admin.agency_run.step_short.${currentStep.step}`),
          })}
        </span>
      )}
    </div>
  );
}

// ── run card (agency drawer) ─────────────────────────────────────────────

const MARKS: Record<StepView, string> = {
  done: "✓",
  now: "●",
  fail: "!",
  pending: "○",
  not_run: "○",
};

function StepResult({
  run,
  step,
  index,
  view,
  latestDataDate,
}: {
  run: AgencyRun;
  step: AgencyRunStep;
  index: number;
  view: StepView;
  latestDataDate: string | null;
}) {
  const { t } = useTranslation();
  if (view === "now") return <Elapsed since={currentSince(run, index)} />;
  if (view === "pending") return <>{t("admin.agency_run.res_waiting")}</>;
  if (view === "not_run") return <>{t("admin.agency_run.res_not_run")}</>;
  if (view === "fail") {
    return (
      <>
        {step.status === "skipped"
          ? t("admin.agency_run.res_skipped")
          : t(`admin.agency_run.res_failed.${step.step}`)}
      </>
    );
  }
  const parts: string[] = [];
  if (step.status === "unchanged") parts.push(t("admin.agency_run.res_unchanged"));
  if (step.step === "static" && step.rows != null) parts.push(t("admin.agency_run.res_routes", { routes: step.rows }));
  if (step.step === "analyze" && latestDataDate) {
    parts.push(t("admin.agency_run.res_through", { date: formatShortDate(latestDataDate) }));
  }
  const seconds = secondsBetween(step.started_at, step.finished_at);
  if (seconds != null) parts.push(formatDuration(seconds));
  return <>{parts.join(" · ")}</>; // i18n-ignore: locale-neutral separator
}

function segLabel(t: (key: string, opts?: Record<string, unknown>) => string, run: AgencyRun, state: RunState) {
  const total = run.steps.length;
  if (state === "done") return t("admin.agency_run.seg_done", { total });
  if (state === "running") return t("admin.agency_run.seg_running", { total, current: currentStepIndex(run) + 1 });
  const stoppedAt = run.steps.findIndex((s) => s.status !== "ok");
  return t("admin.agency_run.seg_stopped", { total, current: (stoppedAt === -1 ? total - 1 : stoppedAt) + 1 });
}

/** An agency's latest run, step by step: a segment per step, then each
 *  step's result, duration or elapsed time. A stopped run says where and
 *  why, and offers to run it again. */
export function AgencyRunCard({
  agencyId,
  agencyName,
  run,
  latestDataDate,
  onRetry,
  retryBlocked,
  onEditUrl,
}: {
  agencyId: number;
  agencyName: string;
  run: AgencyRun;
  latestDataDate: string | null;
  onRetry: () => void;
  retryBlocked: boolean;
  onEditUrl: () => void;
}) {
  const { t } = useTranslation();
  const state = runState(run);
  const current = currentStepIndex(run);
  const views = run.steps.map((step, i) => stepView(step, i === current));
  const failedIndex = run.steps.findIndex((s) => s.status === "error" || s.status === "skipped");
  const failedStep = run.steps[failedIndex] as AgencyRunStep | undefined;
  const detail = failedStep?.error ?? (state === "stopped" ? run.error : null);
  const skipped = run.status === "skipped" || failedStep?.status === "skipped";
  // A re-aggregation keeps going after a failed ingest, so whether the
  // steps after a failure ran is read off the steps, not assumed.
  const wentOn =
    failedIndex !== -1 && run.steps.slice(failedIndex + 1).some((s) => s.status !== "not_run" && s.status !== "pending");

  return (
    <section className="agency-run-card" aria-label={t("admin.agency_run.card_label")}>
      <div className="agency-run-head">
        <b>{agencyName}</b>
        <span className={`agency-run-state agency-run-state--${state}`}>{t(`admin.agency_run.state_${state}`)}</span>
      </div>
      <div className="agency-run-segbar" role="img" aria-label={segLabel(t, run, state)}>
        {views.map((view, i) => (
          <span
            key={run.steps[i].step}
            className={`agency-run-seg${view === "done" || view === "now" || view === "fail" ? ` agency-run-seg--${view}` : ""}`}
          />
        ))}
      </div>
      <ol className="agency-run-steps">
        {run.steps.map((step, i) => (
          <li key={step.step} className={`agency-run-step agency-run-step--${views[i]}`}>
            <span className="agency-run-step-mark" aria-hidden="true">
              {MARKS[views[i]]}
            </span>
            <span>{t(`admin.agency_run.step.${step.step}`)}</span>
            <span className="agency-run-step-res">
              <StepResult run={run} step={step} index={i} view={views[i]} latestDataDate={latestDataDate} />
            </span>
          </li>
        ))}
      </ol>
      {detail && <p className="agency-run-detail">{detail}</p>}
      {state === "running" && <p className="agency-run-foot">{t("admin.agency_run.foot_running")}</p>}
      {state === "done" && (
        <p className="agency-run-foot">
          <span>
            {t("admin.agency_run.foot_done", {
              time: clockTime(run.finished_at),
              duration: formatDuration(secondsBetween(run.started_at, run.finished_at)),
            })}
          </span>
          <Link className="agency-run-link" to={destHref(agencyId, "pulse")}>
            {t("admin.agency_run.open_pulse")}
          </Link>
        </p>
      )}
      {state === "stopped" && (
        <p className="agency-run-foot">
          <span>
            {skipped
              ? t("admin.agency_run.foot_skipped")
              : failedStep?.step === "static"
                ? t("admin.agency_run.foot_static_failed")
                : wentOn
                  ? t("admin.agency_run.foot_failed_continued")
                  : t("admin.agency_run.foot_failed")}
          </span>
          {failedStep?.step === "static" && !skipped && (
            <button type="button" className="agency-run-link" onClick={onEditUrl}>
              {t("admin.agency_run.edit_url")}
            </button>
          )}
          <button type="button" className="agency-run-link" onClick={onRetry} disabled={retryBlocked}>
            {t("admin.agency_run.retry")}
          </button>
        </p>
      )}
    </section>
  );
}
