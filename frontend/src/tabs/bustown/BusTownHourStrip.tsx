import type { TFunction } from "i18next";
import type { ReplayHour } from "../../api/types";
import { formatServiceClock } from "./busTownModel";

type HourSlot = { hour: number; departures: number };

function hourSlots(hours: readonly ReplayHour[], startHour: number, endHour: number): HourSlot[] {
  const byHour = new Map(hours.map((h) => [h.hour, h.departures]));
  const slots: HourSlot[] = [];
  for (let hour = startHour; hour < endHour; hour++) {
    slots.push({ hour, departures: byHour.get(hour) ?? 0 });
  }
  return slots;
}

/** The busiest slot; a tie goes to the earliest hour. */
function busiestSlot(slots: readonly HourSlot[]): HourSlot | undefined {
  let best: HourSlot | undefined;
  for (const slot of slots) {
    if (!best || slot.departures > best.departures) best = slot;
  }
  return best;
}

type Props = {
  hours: readonly ReplayHour[];
  startHour: number;
  endHour: number;
  playheadSec: number;
  t: TFunction;
};

/**
 * A quiet strip under the scrubber: one bar per service-day hour in
 * `[startHour, endHour)`, scaled to the busiest hour in that range, with the
 * playhead's own hour picked out. Renders nothing at all when the day has no
 * hourly aggregate yet, or every hour in range is silent.
 */
export function BusTownHourStrip({ hours, startHour, endHour, playheadSec, t }: Props) {
  const slots = hourSlots(hours, startHour, endHour);
  const busiest = busiestSlot(slots);
  if (!busiest || busiest.departures === 0) return null;

  // Floored, not clamped: clamping would pin the range's last instant onto
  // the final hour's bar, which the playhead has already left.
  const currentHour = Math.floor(playheadSec / 3600);

  return (
    <div className="bus-town-hour-strip">
      <div className="bus-town-hour-bars" aria-hidden="true">
        {slots.map((slot) => (
          <div
            key={slot.hour}
            className={`bus-town-hour-bar${slot.departures > 0 ? " has-value" : ""}${slot.hour === currentHour ? " is-current" : ""}`}
            style={slot.departures > 0 ? { height: `${(slot.departures / busiest.departures) * 100}%` } : undefined}
          />
        ))}
      </div>
      <p className="bus-town-hour-caption">
        {t("busTown.busiest_hour", { time: formatServiceClock(busiest.hour * 3600) })}
      </p>
    </div>
  );
}
