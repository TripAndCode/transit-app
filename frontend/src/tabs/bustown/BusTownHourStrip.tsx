import type { TFunction } from "i18next";
import type { ReplayHour } from "../../api/types";
import { formatServiceClock } from "./busTownModel";

/** Half the range thumb's declared width (`scrubber.css`'s `.scrub-input`
 *  thumb rules), so the bar row's own horizontal inset lands each bar's
 *  centre where the thumb would sit at that hour. */
const THUMB_HALF_WIDTH_PX = 2;

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
function busiestSlot(slots: readonly HourSlot[]): HourSlot {
  let best = slots[0];
  for (const slot of slots) {
    if (slot.departures > best.departures) best = slot;
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
  const maxDepartures = Math.max(0, ...slots.map((s) => s.departures));
  if (maxDepartures === 0) return null;

  const busiest = busiestSlot(slots);
  // Floored, not clamped: at the exact end of the range this names an hour
  // the strip never draws a bar for, so the playhead's last instant simply
  // shows no current bar rather than highlighting the wrong one.
  const currentHour = Math.floor(playheadSec / 3600);

  return (
    <div className="bus-town-hour-strip">
      <div className="bus-town-hour-bars" aria-hidden="true" style={{ paddingInline: THUMB_HALF_WIDTH_PX }}>
        {slots.map((slot) => (
          <div
            key={slot.hour}
            className={`bus-town-hour-bar${slot.departures > 0 ? " has-value" : ""}${slot.hour === currentHour ? " is-current" : ""}`}
            style={slot.departures > 0 ? { height: `${(slot.departures / maxDepartures) * 100}%` } : undefined}
          />
        ))}
      </div>
      <p className="bus-town-hour-caption">
        {t("busTown.busiest_hour", { time: formatServiceClock(busiest.hour * 3600) })}
      </p>
    </div>
  );
}
