import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../../api/hooks";
import { delayRampVar } from "../../styles/tokens";
import { WEEKDAYS, isoDaysBefore, todayISO, type Scope, type ScopePatch, type Weekday } from "../../api/scope";
import type { ScopeSummary } from "../../api/types";
import { useAgencyId } from "../../api/useAgencyId";
import { RoutesPicker } from "../RoutesPicker";
import { buildTimeBandOptions } from "../timeBandOptions";
import { sinceStart } from "./brushMath";
import { PeriodBrush } from "./PeriodBrush";
import { toleranceLabel } from "./scopePhrases";
import "./scope.css";

export type ControlProps = {
  scope: Scope;
  update: (patch: ScopePatch) => void;
  /** The controls' data, while it is loaded; every control works without it. */
  summary?: ScopeSummary;
};

const PERIOD_PRESETS = [7, 14, 30] as const;
const TOLERANCE_PRESETS_MIN = [1, 3, 5] as const;
const DEFAULT_LATE_SEC = 60;
const MAX_LATE_SEC = 600;

function Pill({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" className="scope-pill" aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}

/** A small bar coloured by the delay ramp, with the mean beside it. */
export function DelayBar({ minutes }: { minutes: number }) {
  const { t } = useTranslation();
  return (
    <span className="scope-delay">
      <span className="scope-mini-bar" style={{ background: delayRampVar(minutes) }} aria-hidden="true" />
      {t("scope.control.mean_min", { n: minutes.toFixed(1) })}
    </span>
  );
}

/** Presets end on the agency's latest data day, so "last 7 days" is the last
 *  seven days that have data rather than a week that may not have arrived. */
export function PeriodControl({ scope, update, summary }: ControlProps) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { data: agencies } = useAgencies();
  const anchor = summary?.latest ?? agencies?.find((a) => a.agency_id === id)?.latest_data_date ?? todayISO();
  const collectionStart = summary?.earliest ? sinceStart(summary.earliest, anchor) : null;
  function setDate(edge: "from" | "to", value: string) {
    const next = { from: scope.from, to: scope.to, [edge]: value };
    if (value && next.from <= next.to) update({ [edge]: value });
  }
  return (
    <div className="scope-control">
      <div className="scope-pills">
        {PERIOD_PRESETS.map((days) => {
          const from = isoDaysBefore(anchor, days - 1);
          return (
            <Pill key={days} pressed={scope.from === from && scope.to === anchor} onClick={() => update({ from, to: anchor })}>
              {t("scope.control.last_days", { count: days })}
            </Pill>
          );
        })}
        {collectionStart && (
          <Pill
            pressed={scope.from === collectionStart && scope.to === anchor}
            onClick={() => update({ from: collectionStart, to: anchor })}
          >
            {t("scope.control.since_start")}
          </Pill>
        )}
      </div>
      {summary && summary.days.length > 0 && (
        <PeriodBrush
          days={summary.days}
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

export function DaysControl({ scope, update, summary }: ControlProps) {
  const { t } = useTranslation();
  const serviceId = useId();
  const on = selectedDays(scope.dow);
  const weekdayMeans = new Map(summary?.weekdays.map((w) => [w.dow, w.avg_min]) ?? []);
  function toggle(day: Weekday) {
    const next = new Set(on);
    if (next.has(day)) next.delete(day);
    else next.add(day);
    if (next.size === 0) return;
    update({ dow: WEEKDAYS.filter((d) => next.has(d)).join(",") as Scope["dow"] });
  }
  return (
    <div className="scope-control">
      <div className="scope-pills">
        {(["all", "weekday", "weekend"] as const).map((preset) => (
          <Pill key={preset} pressed={scope.dow === preset} onClick={() => update({ dow: preset })}>
            {t(`scope.control.${preset === "all" ? "days_all" : preset}`)}
          </Pill>
        ))}
      </div>
      <div className="scope-pills">
        {WEEKDAYS.map((day) => {
          const mean = weekdayMeans.get(day);
          return (
            <button key={day} type="button" className="scope-pill" aria-pressed={on.has(day)} onClick={() => toggle(day)}>
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
      <label htmlFor={serviceId} className="scope-field">
        {t("scope.control.service")}
      </label>
      <select id={serviceId} value={scope.service} onChange={(e) => update({ service: e.target.value as Scope["service"] })}>
        <option value="all">{t("filters.service.all")}</option>
        <option value="平日">{t("common.service_value.平日")}</option>{/* i18n-ignore: query contract */}
        <option value="土日祝">{t("common.service_value.土日祝")}</option>{/* i18n-ignore: query contract */}
      </select>
      <WholeDayNote scope={scope} update={update} summary={summary} />
    </div>
  );
}

export function TimeControl({ scope, update }: ControlProps) {
  const { t } = useTranslation();
  return (
    <div className="scope-control">
      <div className="scope-pills">
        {buildTimeBandOptions(t).map((band) => (
          <Pill
            key={band.value}
            pressed={scope.hour == null && scope.time_band === band.value}
            onClick={() => update({ time_band: band.value, hour: null })}
          >
            {band.label}
          </Pill>
        ))}
      </div>
      <p className="scope-note">{t("scope.control.hour_pending")}</p>
    </div>
  );
}

export function RoutesControl({ scope, update, summary }: ControlProps) {
  const delays = summary ? new Map(summary.routes.map((r) => [r.route_code, r])) : undefined;
  return (
    <div className="scope-control">
      <RoutesPicker
        selected={scope.routes}
        onChange={(routes) => update({ routes: routes.length > 0 ? routes : null })}
        delays={delays}
        renderDelay={(minutes) => <DelayBar minutes={minutes} />}
      />
      <WholeDayNote scope={scope} update={update} summary={summary} />
    </div>
  );
}

/** The summary comes from daily aggregates, which have no hour of day; say
 *  so wherever its figures sit beside a time condition they do not reflect. */
function WholeDayNote({ scope, summary }: ControlProps) {
  const { t } = useTranslation();
  if (!summary || (scope.time_band === "all" && scope.hour == null)) return null;
  return <p className="scope-note">{t("scope.control.whole_day")}</p>;
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
      <div className="scope-pills">
        {TOLERANCE_PRESETS_MIN.map((min) => {
          const share = summary?.tolerance.find((p) => p.late_sec === min * 60)?.on_time_pct;
          return (
            <button
              key={min}
              type="button"
              className="scope-pill"
              aria-pressed={current === min * 60}
              onClick={() => write(min * 60)}
            >
              {t("scope.control.minutes", { n: min })}
              {share != null && (
                <>
                  {" "}
                  <span className="scope-share">{t("scope.control.share", { pct: Math.round(share) })}</span>
                </>
              )}
            </button>
          );
        })}
      </div>
      {summary && summary.tolerance.length > 0 && (
        <svg
          role="img"
          aria-label={t("scope.control.curve_label")}
          className="scope-curve"
          viewBox={`0 0 ${CURVE_W} ${CURVE_H}`}
          width="100%"
          height={CURVE_H}
        >
          <polyline
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            points={summary.tolerance.map((p) => `${curveX(p.late_sec)},${curveY(p.on_time_pct)}`).join(" ")}
          />
          <circle
            className="scope-curve__marker"
            cx={curveX(value)}
            cy={curveY(shareAt(summary.tolerance, value) ?? 0)}
            r={3.5}
            fill="var(--accent-strong)"
          />
        </svg>
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
    </div>
  );
}

function ClearValue({ value, onClear }: { value: string; onClear: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="scope-control">
      <span className="scope-field">{value}</span>
      <button type="button" className="scope-pill" onClick={onClear}>
        {t("scope.control.clear")}
      </button>
    </div>
  );
}

export function StopControl({ scope, update }: ControlProps) {
  return <ClearValue value={scope.stop ?? ""} onClear={() => update({ stop: null })} />;
}

export function EarlyControl({ scope, update }: ControlProps) {
  return <ClearValue value={scope.early == null ? "" : String(scope.early)} onClear={() => update({ early: null })} />;
}

export function DirControl({ scope, update }: ControlProps) {
  return <ClearValue value={scope.dir == null ? "" : String(scope.dir)} onClear={() => update({ dir: null })} />;
}
