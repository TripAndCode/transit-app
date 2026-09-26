import type { TFunction } from "i18next";
import { Pin, PinOff } from "lucide-react";
import { InlineSparkline } from "../../components/InlineSparkline";
import { Tooltip } from "../../components/Tooltip";
import type { LiveTrip, LiveTripProgressResponse } from "../../api/types";
import "./inspectCard.css";

/** Mean delay in minutes for each hour the trip reported in, oldest first.
 *  Buckets on absolute hour boundaries rather than a parsed local hour, so
 *  the series is the same series wherever it is read; JST is a whole-hour
 *  offset, so those boundaries are also the operator's clock hours. */
function hourlyDelayMinutes(progress: LiveTripProgressResponse | undefined): number[] {
  const buckets = new Map<number, { total: number; count: number }>();
  for (const stop of progress?.stops ?? []) {
    const reported = new Date(stop.reported_at).getTime();
    if (!Number.isFinite(reported)) continue;
    const hour = Math.floor(reported / 3_600_000);
    const bucket = buckets.get(hour) ?? { total: 0, count: 0 };
    buckets.set(hour, { total: bucket.total + stop.dep_delay, count: bucket.count + 1 });
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, bucket]) => bucket.total / bucket.count / 60);
}

/** The consecutive stop pair that added the most delay. Only a pair that
 *  actually lost time qualifies — on a trip that recovers throughout, naming
 *  its least-improving leg as "worst" would invent a problem. */
function worstSegment(progress: LiveTripProgressResponse | undefined): { from: string; to: string } | null {
  const stops = [...(progress?.stops ?? [])].sort((a, b) => a.stop_sequence - b.stop_sequence);
  let worst: { from: string; to: string } | null = null;
  let lost = 0;
  for (let i = 1; i < stops.length; i += 1) {
    const delta = stops[i].dep_delay - stops[i - 1].dep_delay;
    const from = stops[i - 1].stop_name;
    const to = stops[i].stop_name;
    if (delta > lost && from && to) {
      lost = delta;
      worst = { from, to };
    }
  }
  return worst;
}

/**
 * The operations map's inspect card: one trip, read off the live feed, docked
 * in the map's top-right corner.
 *
 * It replaces a MapLibre `Popup`, which was imperative DOM built once at click
 * time and therefore a snapshot — the thirty-second refetch had to destroy it
 * rather than update it, so watching one trip meant clicking the same vehicle
 * twice a minute. Rendered from the same live rows as the rest of the page, it
 * simply re-renders instead.
 *
 * `pinned` is the difference between a hover preview and a trip the operator
 * chose: a preview shows only the headline, because it appears and disappears
 * under a moving pointer and anything more would flicker.
 *
 * Moving to another vehicle swaps the card's contents outright, behind a
 * crossfade keyed on the trip. Nothing tweens between two trips: a delay
 * figure sliding from one vehicle's value to another's spends the whole
 * sweep displaying a number no vehicle reported.
 *
 * Deliberately not a live region. Its figures change on their own every thirty
 * seconds, so announcing them would read the whole card aloud twice a minute
 * to a reader who never asked for it; it is a labelled complementary landmark
 * to visit instead.
 */
export function InspectCard({ trip, routeName, vehicles, progress, pinned, onPin, onUnpin, t }: {
  trip: LiveTrip;
  routeName: string;
  /** Vehicles currently running this route, the card's one piece of context
   *  that is about the route rather than this trip. */
  vehicles: number;
  progress?: LiveTripProgressResponse;
  pinned: boolean;
  onPin: () => void;
  onUnpin: () => void;
  t: TFunction;
}) {
  const delayMin = trip.dep_delay / 60;
  const hourly = pinned ? hourlyDelayMinutes(progress) : [];
  const segment = pinned ? worstSegment(progress) : null;

  return (
    <aside
      className={`ops-inspect${pinned ? "" : " ops-inspect--preview"}`}
      aria-label={t("operations.inspect.label")}
    >
      {/* Keyed on the trip so a different vehicle remounts the contents and
          replays the fade, while the thirty-second refetch of the same trip
          updates in place -- an entrance twice a minute is the flicker the
          card exists to end. */}
      <div className="ops-inspect__body" key={trip.trip_id}>
        <div className="ops-inspect__head">
          {trip.route_code && <span className="ops-inspect__badge">{trip.route_code}</span>}
          <div className="ops-inspect__name">
            <b>{routeName}</b>
            {trip.headsign && <small>{trip.headsign}</small>}
          </div>
          <Tooltip label={t(pinned ? "operations.inspect.unpin" : "operations.inspect.pin")} placement="left">
            <button
              type="button"
              className="ops-inspect__pin"
              aria-label={t(pinned ? "operations.inspect.unpin" : "operations.inspect.pin")}
              aria-pressed={pinned}
              onClick={pinned ? onUnpin : onPin}
            >
              {pinned ? <PinOff size={14} aria-hidden="true" /> : <Pin size={14} aria-hidden="true" />}
            </button>
          </Tooltip>
        </div>

        <p className="ops-inspect__delay">
          {/* Sign comes from the value, so a trip running early reads "-1.5"
              rather than a caller-supplied "+" colliding with the minus. */}
          <b className="num">{`${delayMin < 0 ? "-" : "+"}${Math.abs(delayMin).toFixed(1)}`}</b>
          <span>{t("operations.inspect.delay_unit")}</span>
        </p>

        {hourly.length >= 2 && (
          <div className="ops-inspect__spark" data-testid="inspect-sparkline">
            <InlineSparkline
              points={hourly}
              width={220}
              height={46}
              accent="var(--accent)"
              forceAccent
              showLabels={false}
              style={{ display: "block", width: "100%", verticalAlign: "baseline" }}
            />
            <small>{t("operations.inspect.hourly_trend")}</small>
          </div>
        )}

        {segment && (
          <p className="ops-inspect__row">
            <span>{t("operations.inspect.worst_segment")}</span>
            <b>{t("operations.inspect.segment", { from: segment.from, to: segment.to })}</b>
          </p>
        )}

        {pinned && (
          <p className="ops-inspect__row">
            <span>{t("operations.inspect.vehicles_label")}</span>
            <b>{t("operations.inspect.vehicles", { vehicles })}</b>
          </p>
        )}
      </div>
    </aside>
  );
}
