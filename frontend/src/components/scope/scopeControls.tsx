import { use, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { delayRampVar } from "../../styles/tokens";
import { DEFAULT_WINDOW, earlyParam, scopeWindow } from "../../api/onTimeDefault";
import { DataEndContext, WEEKDAYS, defaultPeriod, isoDaysBefore, type Scope, type ScopePatch, type Weekday } from "../../api/scope";
import { timetableChoices } from "../../api/serviceDays";
import type { ScopeSummary } from "../../api/types";
import { serviceValueLabel } from "../../utils/filterValueLabels";
import { RoutesPicker } from "../RoutesPicker";
import { Segmented } from "../ui/Segmented";
import { buildTimeBandOptions } from "../timeBandOptions";
import { sinceStart } from "./brushMath";
import { PeriodBrush } from "./PeriodBrush";
import { earlyLabel, toleranceLabel } from "./scopePhrases";
import "./scope.css";
import { fixedTenth, roundTenth } from "../../utils/format";

export type ControlProps = {
  scope: Scope;
  update: (patch: ScopePatch) => void;
  /** The controls' data, while it is loaded; every control works without it. */
  summary?: ScopeSummary;
  /** Closes the popover this control renders in. A control that stages a
   *  draft uses it so its Apply commits and dismisses together. Absent for a
   *  control rendered pinned in the scope strip, which has no popover to
   *  close. */
  close?: () => void;
};

const PERIOD_PRESETS = [7, 14, 30] as const;
const TOLERANCE_PRESETS_MIN = [1, 3, 5] as const;
const DEFAULT_LATE_SEC = DEFAULT_WINDOW.late_sec;
const MAX_LATE_SEC = 600;
/** The early tolerance the early-departure choice offers: the industry
 *  window's, one minute. */
const EARLY_LIMIT_SEC = 60;

/** A small bar coloured by the delay ramp, with the mean beside it. */
function DelayBar({ minutes }: { minutes: number }) {
  const { t } = useTranslation();
  return (
    <span className="scope-delay">
      <span className="scope-mini-bar" style={{ background: delayRampVar(minutes) }} aria-hidden="true" />
      {t("scope.control.mean_min", { n: fixedTenth(minutes) })}
    </span>
  );
}

/** Presets end where the default period ends: the agency's latest data day,
 *  or the last closed day when the data runs into today. "Last 7 days" is
 *  then the last seven days that have data, never a week that has not
 *  arrived or a day still under way, and the default period is one of the
 *  presets. */
export function PeriodControl({ scope, update, summary }: ControlProps) {
  const { t } = useTranslation();
  const anchor = defaultPeriod(use(DataEndContext)).to;
  const collectionStart = summary?.earliest ? sinceStart(summary.earliest, anchor) : null;
  const presetChoices = PERIOD_PRESETS.map((days) => ({
    value: `last-${days}`,
    label: t("scope.control.last_days", { count: days }),
    from: isoDaysBefore(anchor, days - 1),
    to: anchor,
  }));
  // One choice per range: a single-choice group can check only one of two
  // identical ranges, so the collection choice gives way to a preset that
  // spans the same days.
  const periodChoices =
    collectionStart && !presetChoices.some((choice) => choice.from === collectionStart)
      ? [...presetChoices, { value: "since-start", label: t("scope.control.since_start"), from: collectionStart, to: anchor }]
      : presetChoices;
  const periodValue = periodChoices.find((choice) => choice.from === scope.from && choice.to === scope.to)?.value ?? null;
  function setDate(edge: "from" | "to", value: string) {
    const next = { from: scope.from, to: scope.to, [edge]: value };
    if (value && next.from <= next.to) update({ [edge]: value });
  }
  return (
    <div className="scope-control">
      <Segmented
        aria-label={t("scope.popover.period")}
        options={periodChoices}
        // Two to a row, for the same narrow popover as the time bands.
        columns={2}
        value={periodValue}
        onChange={(value) => {
          const choice = periodChoices.find((c) => c.value === value);
          if (choice) update({ from: choice.from, to: choice.to });
        }}
      />
      {summary && summary.days.length > 0 && (
        <PeriodBrush
          days={summary.days}
          windowFrom={summary.window_from}
          latest={anchor}
          from={scope.from}
          to={scope.to}
          onCommit={(from, to) => update({ from, to })}
        />
      )}
      <WholeDayNote scope={scope} update={update} summary={summary} />
      <div className="scope-dates">
        <label>
          {t("scope.control.from")}
          <input type="date" value={scope.from} onChange={(e) => setDate("from", e.target.value)} />
        </label>
        <label>
          {t("scope.control.to")}
          <input type="date" value={scope.to} onChange={(e) => setDate("to", e.target.value)} />
        </label>
      </div>
    </div>
  );
}

function selectedDays(dow: Scope["dow"]): Set<Weekday> {
  if (dow === "all") return new Set(WEEKDAYS);
  if (dow === "weekday") return new Set(WEEKDAYS.slice(0, 5));
  if (dow === "weekend") return new Set(WEEKDAYS.slice(5));
  return new Set(dow.split(",") as Weekday[]);
}

const DOW_PRESETS = ["all", "weekday", "weekend"] as const;

function dowPreset(dow: Scope["dow"]): (typeof DOW_PRESETS)[number] | null {
  return (DOW_PRESETS as readonly string[]).includes(dow) ? (dow as (typeof DOW_PRESETS)[number]) : null;
}

function sameDays(a: Set<Weekday>, b: Set<Weekday>): boolean {
  if (a.size !== b.size) return false;
  for (const day of a) if (!b.has(day)) return false;
  return true;
}

/** The Cancel/Apply footer of a control that stages a draft. Apply is
 *  disabled while the draft matches what is committed; discarding,
 *  committing and closing the popover are the caller's, through `onCancel`
 *  and `onApply`. */
function ApplyFoot({
  hint,
  applyLabel,
  disabled,
  onApply,
  onCancel,
}: {
  hint?: string;
  applyLabel: string;
  disabled: boolean;
  onApply: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="scope-apply">
      {hint && <span className="scope-apply__hint">{hint}</span>}
      <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
        {t("common.cancel")}
      </button>
      <button type="button" className="btn btn--primary btn--sm scope-apply__button" disabled={disabled} onClick={onApply}>
        {applyLabel}
      </button>
    </div>
  );
}

/** A draft of the staged weekdays, committed only on Apply. When the
 *  committed days change outside this control (a reset, or the pinned
 *  strip and an open popover sharing one scope), the draft starts over from
 *  them during render. A preset pick is instant, as the timetable select
 *  already is, and replaces the draft too so a stale toggle can't linger
 *  under a freshly picked preset. No preset shows as checked while a draft is
 *  pending, since the chips no longer match the committed days. */
export function DaysControl({ scope, update, summary, close }: ControlProps) {
  const { t } = useTranslation();
  const serviceId = useId();
  const committedDays = selectedDays(scope.dow);
  const [draft, setDraft] = useState<Set<Weekday>>(() => committedDays);
  const [committed, setCommitted] = useState(scope.dow);
  if (committed !== scope.dow) {
    setCommitted(scope.dow);
    setDraft(committedDays);
  }
  const weekdayMeans = new Map(summary?.weekdays.map((w) => [w.dow, w.avg_min]) ?? []);
  const timetable = timetableChoices(scope.service, summary?.day_types);
  function toggle(day: Weekday) {
    const next = new Set(draft);
    if (next.has(day)) next.delete(day);
    else next.add(day);
    if (next.size === 0) return;
    setDraft(next);
  }
  const unchanged = sameDays(draft, committedDays);
  function commit() {
    if (unchanged) return;
    update({ dow: WEEKDAYS.filter((d) => draft.has(d)).join(",") as Scope["dow"] });
    close?.();
  }
  function cancel() {
    setDraft(committedDays);
    close?.();
  }
  return (
    <div className="scope-control">
      <Segmented
        aria-label={t("scope.popover.days")}
        options={DOW_PRESETS.map((preset) => ({ value: preset, label: t(`scope.control.${preset === "all" ? "days_all" : preset}`) }))}
        value={unchanged ? dowPreset(scope.dow) : null}
        onChange={(preset) => {
          update({ dow: preset });
          setDraft(selectedDays(preset));
        }}
      />
      <div className="scope-days">
        {WEEKDAYS.map((day) => {
          const mean = weekdayMeans.get(day);
          return (
            <button
              key={day}
              type="button"
              className="btn btn--secondary btn--sm"
              aria-pressed={draft.has(day)}
              onClick={() => toggle(day)}
            >
              {t(`forecast.dow_${day}`)}
              {mean != null && (
                <>
                  {" "}
                  <DelayBar minutes={mean} />
                </>
              )}
            </button>
          );
        })}
      </div>
      {weekdayMeans.size > 0 && <p className="scope-note">{t("scope.control.weekday_colour")}</p>}
      <label htmlFor={serviceId} className="scope-field">
        {t("scope.control.service")}
      </label>
      <select id={serviceId} value={timetable.shown} onChange={(e) => update({ service: e.target.value as Scope["service"] })}>
        <option value="all">{t("filters.service.all")}</option>
        {timetable.options.map((service) => (
          <option key={service} value={service}>
            {serviceValueLabel(service, t)}
          </option>
        ))}
      </select>
      <WholeDayNote scope={scope} update={update} summary={summary} />
      <ApplyFoot applyLabel={t("scope.control.days_apply", { count: draft.size })} disabled={unchanged} onApply={commit} onCancel={cancel} />
    </div>
  );
}

export function TimeControl({ scope, update }: ControlProps) {
  const { t } = useTranslation();
  return (
    <div className="scope-control">
      <Segmented
        aria-label={t("scope.popover.time")}
        options={buildTimeBandOptions(t)}
        value={scope.hour == null ? scope.time_band : null}
        onChange={(value) => update({ time_band: value, hour: null })}
        // Two to a row: the popover is narrow at every width, and four en
        // labels such as "Late morning (09–12)" don't fit side by side in it.
        columns={2}
      />
    </div>
  );
}

function sameRoutes(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = new Set(a);
  return b.every((code) => sa.has(code));
}

/** A draft of the picked routes, committed only on Apply. When the
 *  committed routes change outside this control (a reset, or the pinned
 *  strip and an open popover sharing one scope), the draft starts over from
 *  them during render; the picker's search and expanded lines stay as the
 *  user left them. */
export function RoutesControl({ scope, update, summary, close }: ControlProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string[]>(() => scope.routes);
  const [committed, setCommitted] = useState(scope.routes);
  if (!sameRoutes(committed, scope.routes)) {
    setCommitted(scope.routes);
    setDraft(scope.routes);
  }
  const delays = summary ? new Map(summary.routes.map((r) => [r.route_code, r])) : undefined;
  const unchanged = sameRoutes(draft, scope.routes);
  function commit() {
    if (unchanged) return;
    update({ routes: draft.length > 0 ? draft : null });
    close?.();
  }
  function cancel() {
    setDraft(scope.routes);
    close?.();
  }
  const applyLabel =
    draft.length === 0 ? t("filters.routes.apply_all") : t("filters.routes.apply_count", { count: draft.length });
  return (
    <div className="scope-control">
      <RoutesPicker
        selected={draft}
        onChange={setDraft}
        delays={delays}
        renderDelay={(minutes) => <DelayBar minutes={minutes} />}
        tray
        onSubmit={commit}
      />
      <WholeDayNote scope={scope} update={update} summary={summary} />
      <ApplyFoot hint={t("filters.routes.apply_hint")} applyLabel={applyLabel} disabled={unchanged} onApply={commit} onCancel={cancel} />
    </div>
  );
}

/** The summary comes from daily aggregates, which have no hour of day; say
 *  so wherever its figures sit beside a time condition they do not reflect. */
function WholeDayNote({ scope, summary }: ControlProps) {
  const { t } = useTranslation();
  const hasFigures =
    summary != null &&
    (summary.days.length > 0 || summary.weekdays.length > 0 || summary.routes.length > 0 || summary.tolerance.length > 0);
  const placeSet = scope.stop != null || scope.dir != null;
  if (!hasFigures || (!placeSet && scope.time_band === "all" && scope.hour == null)) return null;
  return <p className="scope-note">{t(placeSet ? "scope.control.whole_day_place" : "scope.control.whole_day")}</p>;
}

const CURVE_W = 240;
const CURVE_H = 56;

function curveX(lateSec: number): number {
  return (lateSec / MAX_LATE_SEC) * CURVE_W;
}

function curveY(pct: number): number {
  return CURVE_H - (pct / 100) * CURVE_H;
}

/** The share at `sec`, interpolated between the summary's steps. */
function shareAt(points: ScopeSummary["tolerance"], sec: number): number | null {
  if (points.length === 0) return null;
  const after = points.findIndex((p) => p.late_sec >= sec);
  if (after === -1) return points[points.length - 1].on_time_pct;
  if (after === 0 || points[after].late_sec === sec) return points[after].on_time_pct;
  const a = points[after - 1];
  const b = points[after];
  return a.on_time_pct + ((b.on_time_pct - a.on_time_pct) * (sec - a.late_sec)) / (b.late_sec - a.late_sec);
}

/** The slider writes when the drag ends, not on every step, so dragging
 *  across the range does not refetch the screen at each stop. */
export function ToleranceControl({ scope, update, summary }: ControlProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<number | null>(null);
  const current = scope.late ?? DEFAULT_LATE_SEC;
  const value = draft ?? current;
  function write(sec: number) {
    update({ late: sec === DEFAULT_LATE_SEC ? null : sec });
  }
  function commit() {
    if (draft == null) return;
    write(draft);
    setDraft(null);
  }
  return (
    <div className="scope-control">
      {/* Marks the applied tolerance, not the slider's draft. */}
      <Segmented
        aria-label={t("scope.control.tolerance")}
        options={TOLERANCE_PRESETS_MIN.map((min) => {
          const share = summary?.tolerance.find((p) => p.late_sec === min * 60)?.on_time_pct;
          return {
            value: String(min),
            label: t("scope.control.minutes", { n: min }),
            meta: share == null ? undefined : t("scope.control.share", { pct: Math.round(share) }),
          };
        })}
        value={TOLERANCE_PRESETS_MIN.find((min) => min * 60 === current)?.toString() ?? null}
        onChange={(min) => write(Number(min) * 60)}
      />
      {summary && summary.tolerance.length > 0 && (
        <>
          {/* Stretched to the slider's full width, so the marker sits above
              the thumb. */}
          <svg
            role="img"
            aria-label={t("scope.control.curve_label")}
            className="scope-curve"
            viewBox={`0 0 ${CURVE_W} ${CURVE_H}`}
            preserveAspectRatio="none"
            width="100%"
            height={CURVE_H}
          >
            <polyline
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
              points={summary.tolerance.map((p) => `${curveX(p.late_sec)},${curveY(p.on_time_pct)}`).join(" ")}
            />
            <circle
              className="scope-curve__marker"
              cx={curveX(Math.min(value, MAX_LATE_SEC))}
              cy={curveY(shareAt(summary.tolerance, value) ?? 0)}
              r={3.5}
              fill="var(--accent-strong)"
            />
          </svg>
          <div className="scope-curve-axis" aria-hidden="true">
            <span>{t("scope.control.minutes", { n: 0 })}</span>
            <span>{t("scope.control.axis_lateness")}</span>
            <span>{t("scope.control.minutes", { n: MAX_LATE_SEC / 60 })}</span>
          </div>
          <p className="scope-readout">
            {t("scope.control.readout", {
              n: roundTenth(value / 60),
              pct: Math.round(shareAt(summary.tolerance, value) ?? 0),
            })}
          </p>
        </>
      )}
      <WholeDayNote scope={scope} update={update} summary={summary} />
      <input
        type="range"
        min={0}
        max={MAX_LATE_SEC}
        step={30}
        value={value}
        aria-label={t("scope.control.tolerance")}
        aria-valuetext={toleranceLabel(value, t)}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <EarlyChoice scope={scope} update={update} />
    </div>
  );
}

/** Whether an early departure counts as on time: none more than a minute
 *  early, which with the five-minute late tolerance is the window operators
 *  publish and the default, or every one, which with the one-minute late
 *  tolerance is the legacy definition. An early tolerance a link set to
 *  anything else presses neither and is named beneath them. */
function EarlyChoice({ scope, update }: ControlProps) {
  const { t } = useTranslation();
  const early = scopeWindow(scope).early_sec;
  const earlyLabelText = t("scope.control.early_label");
  const earlyValue = early == null ? "counted" : early === EARLY_LIMIT_SEC ? "limited" : null;
  return (
    <>
      <span className="scope-field">{earlyLabelText}</span>
      <Segmented
        aria-label={earlyLabelText}
        options={[
          { value: "counted", label: t("scope.control.early_counted") },
          { value: "limited", label: t("scope.control.early_limited", { n: EARLY_LIMIT_SEC / 60 }) },
        ]}
        value={earlyValue}
        onChange={(value) => update({ early: value === "counted" ? earlyParam(null) : earlyParam(EARLY_LIMIT_SEC) })}
        // One to a row: each choice is a sentence, too long to share a row.
        columns={1}
      />
      {early != null && early !== EARLY_LIMIT_SEC && (
        <p className="scope-note">{t("scope.control.early_current", { value: earlyLabel(early, t) })}</p>
      )}
    </>
  );
}

function ClearValue({ value, onClear }: { value: string; onClear: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="scope-control">
      <span className="scope-field">{value}</span>
      <button type="button" className="btn btn--secondary btn--sm" onClick={onClear}>
        {t("scope.control.clear")}
      </button>
    </div>
  );
}

export function StopControl({ scope, update }: ControlProps) {
  return <ClearValue value={scope.stop ?? ""} onClear={() => update({ stop: null })} />;
}

export function EarlyControl({ scope, update }: ControlProps) {
  return (
    <div className="scope-control">
      <EarlyChoice scope={scope} update={update} />
    </div>
  );
}

export function DirControl({ scope, update }: ControlProps) {
  return <ClearValue value={scope.dir == null ? "" : String(scope.dir)} onClear={() => update({ dir: null })} />;
}
