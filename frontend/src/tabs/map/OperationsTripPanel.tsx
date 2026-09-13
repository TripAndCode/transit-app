import type { TFunction } from "i18next";
import { BusFront, ChevronRight, MapPin, Radio, Route as RouteIcon, TrendingDown, TrendingUp } from "lucide-react";
import type { LiveTrip, LiveTripProgressResponse } from "../../api/types";
import { relativeTime } from "../../utils/relativeTime";
import { signedMin } from "../live/signedMin";

export type DirectionOption = { key: string; label: string; trips: LiveTrip[] };
export type ActiveRouteOption = { code: string; label: string; trips: number; directions: number; maxDelay: number };

type Props = {
  routeName: string;
  activeRoutes: ActiveRouteOption[];
  directions: DirectionOption[];
  selectedDirection: string | null;
  trips: LiveTrip[];
  selectedTripId: string | null;
  progress: LiveTripProgressResponse | undefined;
  progressLoading: boolean;
  onSelectDirection: (key: string) => void;
  onSelectRoute: (routeCode: string) => void;
  onSelectTrip: (trip: LiveTrip) => void;
  t: TFunction;
};

function departure(trip: LiveTrip): string {
  return trip.scheduled_time?.slice(0, 5) ?? "--:--";
}

function largestChange(stops: LiveTripProgressResponse["stops"]) {
  let growth: { stop: string; seconds: number } | null = null;
  let recovery: { stop: string; seconds: number } | null = null;
  for (let index = 1; index < stops.length; index += 1) {
    const seconds = stops[index].dep_delay - stops[index - 1].dep_delay;
    const item = { stop: stops[index].stop_name ?? `#${stops[index].stop_sequence}`, seconds: Math.abs(seconds) };
    if (seconds > 0 && (!growth || item.seconds > growth.seconds)) growth = item;
    if (seconds < 0 && (!recovery || item.seconds > recovery.seconds)) recovery = item;
  }
  return { growth, recovery };
}

function DelayChart({ progress, t }: { progress: LiveTripProgressResponse; t: TFunction }) {
  const stops = progress.stops;
  if (stops.length < 2) return null;
  const width = 320;
  const height = 82;
  const delays = stops.map((stop) => stop.dep_delay / 60);
  const low = Math.min(0, ...delays);
  const high = Math.max(1, ...delays);
  const range = high - low || 1;
  const points = delays.map((delay, index) => {
    const x = stops.length === 1 ? 0 : (index / (stops.length - 1)) * width;
    const y = height - ((delay - low) / range) * (height - 12) - 6;
    return `${x},${y}`;
  }).join(" ");
  const changes = largestChange(stops);
  return (
    <div className="ops-delay-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("operations.trip_panel.chart_label")}>
        <line x1="0" x2={width} y1={height - 6} y2={height - 6} />
        <polyline points={points} />
        {points.split(" ").map((point, index) => {
          const [cx, cy] = point.split(",");
          return <circle key={stops[index].stop_sequence} cx={cx} cy={cy} r="4" />;
        })}
      </svg>
      <div className="ops-delay-insights">
        {changes.growth && changes.growth.seconds >= 60 && (
          <span className="is-growth"><TrendingUp size={13} />{t("operations.trip_panel.growth", { stop: changes.growth.stop, delay: signedMin(changes.growth.seconds, t) })}</span>
        )}
        {changes.recovery && changes.recovery.seconds >= 60 && (
          <span className="is-recovery"><TrendingDown size={13} />{t("operations.trip_panel.recovery", { stop: changes.recovery.stop, delay: Math.round(changes.recovery.seconds / 60) })}</span>
        )}
      </div>
    </div>
  );
}

export function OperationsTripPanel({
  routeName,
  activeRoutes,
  directions,
  selectedDirection,
  trips,
  selectedTripId,
  progress,
  progressLoading,
  onSelectDirection,
  onSelectRoute,
  onSelectTrip,
  t,
}: Props) {
  const selected = trips.find((trip) => trip.trip_id === selectedTripId) ?? null;
  return (
    <aside className="ops-trip-panel" aria-label={t("operations.trip_panel.aria_label")}>
      <header className="ops-trip-panel__header">
        <div>
          <span className="ops-eyebrow">{t("operations.trip_panel.eyebrow")}</span>
          <h2>{routeName}</h2>
        </div>
        <span>{t("operations.trip_panel.trip_count", {
          count: selectedDirection ? trips.length : activeRoutes.reduce((total, route) => total + route.trips, 0),
        })}</span>
      </header>

      {directions.length > 0 && (
        <div className="ops-directions" role="tablist" aria-label={t("operations.trip_panel.direction_label")}>
          {directions.map((direction) => (
            <button
              key={direction.key}
              type="button"
              role="tab"
              aria-selected={direction.key === selectedDirection}
              onClick={() => onSelectDirection(direction.key)}
            >
              {direction.label}<small>{direction.trips.length}</small>
            </button>
          ))}
        </div>
      )}

      {!selectedDirection && activeRoutes.length > 0 ? (
        <section className="ops-route-browser">
          <div className="ops-route-browser__intro">
            <RouteIcon size={16} />
            <span><strong>{t("operations.trip_panel.choose_route")}</strong><small>{t("operations.trip_panel.choose_route_hint")}</small></span>
          </div>
          <div className="ops-route-browser__list">
            {activeRoutes.map((route) => (
              <button key={route.code} type="button" onClick={() => onSelectRoute(route.code)}>
                <span className="ops-route-browser__badge">{route.code.slice(0, 4)}</span>
                <span><strong>{route.label}</strong><small>{t("operations.trip_panel.route_meta", { trips: route.trips, directions: route.directions })}</small></span>
                <b>{signedMin(route.maxDelay, t)}</b>
                <ChevronRight size={15} />
              </button>
            ))}
          </div>
        </section>
      ) : selected ? (
        <section className="ops-selected-trip">
          <div className="ops-selected-trip__summary">
            <BusFront size={20} aria-hidden="true" />
            <div><strong>{t("operations.trip_panel.departure", { time: departure(selected) })}</strong><span>{selected.headsign ?? t("operations.trip_panel.unknown_direction")}</span></div>
            <b>{signedMin(selected.dep_delay, t)}</b>
          </div>
          <div className="ops-selected-trip__reported">
            <MapPin size={14} />
            <span>{selected.stop_name ?? t("operations.queue.location_unavailable")}</span>
            <small>{t("operations.trip_panel.reported", { when: relativeTime(selected.captured_at) })}</small>
          </div>
          <div className="ops-progress-heading">
            <div><Radio size={15} /><strong>{t("operations.trip_panel.progress_title")}</strong></div>
            <small>{t("operations.trip_panel.progress_disclosure")}</small>
          </div>
          {progressLoading && <div className="ops-progress-loading">{t("operations.trip_panel.progress_loading")}</div>}
          {!progressLoading && progress && progress.stops.length === 0 && <div className="ops-progress-loading">{t("operations.trip_panel.no_progress")}</div>}
          {progress && progress.stops.length > 0 && (
            <>
              <ol className="ops-stop-timeline">
                {progress.stops.map((stop, index) => (
                  <li key={stop.stop_sequence} className={index === progress.stops.length - 1 ? "is-latest" : ""}>
                    <i aria-hidden="true" />
                    <span>{stop.stop_name ?? `#${stop.stop_sequence}`}</span>
                    <b>{signedMin(stop.dep_delay, t)}</b>
                  </li>
                ))}
              </ol>
              <DelayChart progress={progress} t={t} />
            </>
          )}
        </section>
      ) : (
        <div className="ops-trip-panel__empty">{t("operations.trip_panel.select_route")}</div>
      )}

      {trips.length > 1 && (
        <section className="ops-other-trips">
          <h3>{t("operations.trip_panel.other_trips")}</h3>
          {trips.filter((trip) => trip.trip_id !== selectedTripId).map((trip) => (
            <button key={trip.trip_id} type="button" onClick={() => onSelectTrip(trip)}>
              <BusFront size={16} />
              <span><strong>{t("operations.trip_panel.departure", { time: departure(trip) })}</strong><small>{trip.stop_name ?? trip.headsign ?? "-"}</small></span>
              <b>{signedMin(trip.dep_delay, t)}</b>
              <ChevronRight size={15} />
            </button>
          ))}
        </section>
      )}
    </aside>
  );
}
