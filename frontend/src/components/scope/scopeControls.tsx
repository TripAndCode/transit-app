import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../../api/hooks";
import { WEEKDAYS, isoDaysBefore, todayISO, type Scope, type ScopePatch, type Weekday } from "../../api/scope";
import type { ScopeSummary } from "../../api/types";
import { useAgencyId } from "../../api/useAgencyId";
import { RoutesPicker } from "../RoutesPicker";
import { buildTimeBandOptions } from "../timeBandOptions";
import { toleranceLabel } from "./scopePhrases";
import "./scope.css";

export type ControlProps = {
  scope: Scope;
  update: (patch: ScopePatch) => void;
  /** The controls' data, while it is loaded; every control works without it. */
  summary?: ScopeSummary;
};

const PERIOD_PRESETS = [7, 30, 90] as const;
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

/** Presets end on the agency's latest data day, so "last 7 days" is the last
 *  seven days that have data rather than a week that may not have arrived. */
export function PeriodControl({ scope, update }: ControlProps) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { data: agencies } = useAgencies();
  const anchor = agencies?.find((a) => a.agency_id === id)?.latest_data_date ?? todayISO();
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
      </div>
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

export function DaysControl({ scope, update }: ControlProps) {
  const { t } = useTranslation();
  const serviceId = useId();
  const on = selectedDays(scope.dow);
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
        {WEEKDAYS.map((day) => (
          <Pill key={day} pressed={on.has(day)} onClick={() => toggle(day)}>
            {t(`forecast.dow_${day}`)}
          </Pill>
        ))}
      </div>
      <label htmlFor={serviceId} className="scope-field">
        {t("scope.control.service")}
      </label>
      <select id={serviceId} value={scope.service} onChange={(e) => update({ service: e.target.value as Scope["service"] })}>
        <option value="all">{t("filters.service.all")}</option>
        <option value="平日">{t("common.service_value.平日")}</option>{/* i18n-ignore: query contract */}
        <option value="土日祝">{t("common.service_value.土日祝")}</option>{/* i18n-ignore: query contract */}
      </select>
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

export function RoutesControl({ scope, update }: ControlProps) {
  return (
    <div className="scope-control">
      <RoutesPicker selected={scope.routes} onChange={(routes) => update({ routes: routes.length > 0 ? routes : null })} />
    </div>
  );
}

/** The slider writes when the drag ends, not on every step, so dragging
 *  across the range does not refetch the screen at each stop. */
export function ToleranceControl({ scope, update }: ControlProps) {
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
        {TOLERANCE_PRESETS_MIN.map((min) => (
          <Pill key={min} pressed={current === min * 60} onClick={() => write(min * 60)}>
            {t("scope.control.minutes", { n: min })}
          </Pill>
        ))}
      </div>
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
