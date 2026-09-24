import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TimeBand } from "../../api/rangeContext";
import type { RouteTrip } from "../../api/types";
import { StopRibbon } from "./StopRibbon";
import {
  MUTED_OPACITY,
  formatClock,
  hourTicks,
  peakWindow,
  ribbonSegments,
  segmentColor,
  seqToY,
  timeToX,
  timeWindowForBand,
  tripDeparture,
  tripTerminalDelay,
  tripsInWindow,
  type MareyStop,
  type Plot,
  type TimeWindow,
} from "./mareyLayout";

const PLOT: Plot = { left: 108, top: 16, width: 632, height: 268 };
const VIEW_HEIGHT = 322;

/** Ghost trips sit behind the current day as context, not as a second reading. */
const GHOST_OPACITY = 0.35;

/** Most y-axis labels a stop column can carry before they collide. */
const MAX_STOP_LABELS = 14;

type Point = { x: number; y: number; delaySec: number };

function tripPoints(trip: RouteTrip, axis: MareyStop[], window: TimeWindow): Point[] {
  const points: Point[] = [];
  for (const stop of trip.stops) {
    const at = stop.observed_sec ?? stop.scheduled_sec;
    const y = seqToY(stop.stop_sequence, axis, PLOT);
    if (at == null || y == null) continue;
    points.push({ x: timeToX(at, window, PLOT), y, delaySec: stop.delay_sec });
  }
  return points;
}

function tripSummary(trip: RouteTrip, t: (key: string) => string): string {
  const departure = tripDeparture(trip);
  const terminal = tripTerminalDelay(trip);
  return (
    `${t("mareyDeparture")} ${departure == null ? "—" : formatClock(departure)} · ` +
    `${t("mareyTerminal")} ${terminal == null ? "—" : (terminal / 60).toFixed(1)} ${t("minutes")}`
  );
}

/** Time–distance (Marey) diagram: stops down the y axis, clock across the x
 *  axis, one polyline per trip. A leg's colour is the delay at the stop it
 *  arrives at, so a line shading from green to red shows exactly where the
 *  journey lost its time. */
export function MareyDiagram({
  trips,
  previousTrips = [],
  axis,
  band,
  truncated = false,
  date = null,
}: {
  trips: RouteTrip[];
  /** The same route one week earlier, drawn as faint grey context. */
  previousTrips?: RouteTrip[];
  axis: MareyStop[];
  band: TimeBand;
  truncated?: boolean;
  date?: string | null;
}) {
  const { t } = useTranslation("design");
  const [hovered, setHovered] = useState<string | null>(null);
  // useId's own value contains colons, which are not usable inside url(#...).
  const clipId = `marey-clip-${useId().replace(/:/g, "")}`;

  const window = timeWindowForBand(band);
  const drawn = tripsInWindow(trips, window);
  const ghosts = tripsInWindow(previousTrips, window);
  const peak = peakWindow(drawn, window);
  const hoveredTrip = drawn.find((trip) => trip.trip_id === hovered) ?? null;
  const labelStep = Math.max(1, Math.ceil(axis.length / MAX_STOP_LABELS));
  const ribbonWindow = peak ?? window;
  const ribbonLabel = t("ribbonLabel", {
    from: formatClock(ribbonWindow.startSec),
    to: formatClock(ribbonWindow.endSec),
  });

  return (
    <div className="marey">
      <p className="focus-muted marey__caption">
        {t("mareyWindow", { from: formatClock(window.startSec), to: formatClock(window.endSec) })} ·{" "}
        {t("mareyTripCount", { n: drawn.length })}
        {date ? ` · ${date}` : ""}
        {peak ? ` · ${t("mareyPeak")} ${formatClock(peak.startSec)}–${formatClock(peak.endSec)}` : ""}
      </p>
      {truncated && (
        <p className="focus-muted marey__caption" data-testid="marey-truncated">
          {t("mareyTruncated", { n: trips.length })}
        </p>
      )}
      {drawn.length === 0 && ghosts.length === 0 ? (
        <p className="focus-muted">{t("mareyEmpty")}</p>
      ) : (
        <>
          <svg className="marey__chart" viewBox={`0 0 780 ${VIEW_HEIGHT}`} role="group" aria-label={t("tabMarey")}>
            <defs>
              <clipPath id={clipId}>
                <rect x={PLOT.left} y={PLOT.top - 6} width={PLOT.width} height={PLOT.height + 12} />
              </clipPath>
            </defs>
            {peak && (
              <rect
                data-testid="marey-peak"
                x={Math.max(PLOT.left, timeToX(peak.startSec, window, PLOT))}
                y={PLOT.top}
                width={Math.max(
                  0,
                  Math.min(PLOT.left + PLOT.width, timeToX(peak.endSec, window, PLOT)) -
                    Math.max(PLOT.left, timeToX(peak.startSec, window, PLOT)),
                )}
                height={PLOT.height}
                fill="var(--accent-soft)"
              />
            )}
            {hourTicks(window).map((sec) => (
              <g key={sec}>
                <line
                  x1={timeToX(sec, window, PLOT)}
                  y1={PLOT.top}
                  x2={timeToX(sec, window, PLOT)}
                  y2={PLOT.top + PLOT.height}
                  stroke="var(--border-soft)"
                />
                <text
                  x={timeToX(sec, window, PLOT)}
                  y={PLOT.top + PLOT.height + 18}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--text-secondary)"
                >
                  {formatClock(sec)}
                </text>
              </g>
            ))}
            {axis.map((stop, index) => {
              const y = seqToY(stop.stop_sequence, axis, PLOT) ?? PLOT.top;
              return (
                <g key={stop.stop_sequence}>
                  <line x1={PLOT.left} y1={y} x2={PLOT.left + PLOT.width} y2={y} stroke="var(--border-soft)" />
                  {(index % labelStep === 0 || index === axis.length - 1) && (
                    <text x={PLOT.left - 10} y={y + 4} textAnchor="end" fontSize={11} fill="var(--text-secondary)">
                      {stop.stop_name}
                    </text>
                  )}
                </g>
              );
            })}
            <g clipPath={`url(#${clipId})`}>
              {ghosts.map((trip) => {
                const points = tripPoints(trip, axis, window);
                return (
                  <g key={trip.trip_id} data-ghost="true" className="marey-trip" opacity={GHOST_OPACITY}>
                    {points.slice(1).map((point, i) => (
                      <line
                        key={`${trip.trip_id}-${i}`}
                        x1={points[i].x}
                        y1={points[i].y}
                        x2={point.x}
                        y2={point.y}
                        stroke="var(--text-tertiary)"
                        strokeWidth={1.5}
                      />
                    ))}
                  </g>
                );
              })}
              {drawn.map((trip) => {
                const points = tripPoints(trip, axis, window);
                return (
                  <g
                    key={trip.trip_id}
                    data-trip-id={trip.trip_id}
                    className="marey-trip"
                    opacity={hovered !== null && hovered !== trip.trip_id ? MUTED_OPACITY : 1}
                  >
                    {points.slice(1).map((point, i) => (
                      <line
                        key={`${trip.trip_id}-${i}`}
                        x1={points[i].x}
                        y1={points[i].y}
                        x2={point.x}
                        y2={point.y}
                        stroke={segmentColor(point.delaySec)}
                        strokeWidth={2}
                        strokeLinecap="round"
                      />
                    ))}
                    <polyline
                      className="marey-trip__hit"
                      points={points.map((p) => `${p.x},${p.y}`).join(" ")}
                      fill="none"
                      stroke="transparent"
                      strokeWidth={12}
                      onMouseEnter={() => setHovered(trip.trip_id)}
                      onMouseLeave={() => setHovered(null)}
                    >
                      <title>{tripSummary(trip, t)}</title>
                    </polyline>
                  </g>
                );
              })}
            </g>
          </svg>
          <p className="focus-muted marey__readout" data-testid="marey-readout">
            {hoveredTrip ? tripSummary(hoveredTrip, t) : t("mareyHover")}
          </p>
          {drawn.length > 0 && (
            <>
              <p className="focus-muted marey__caption">{ribbonLabel}</p>
              <StopRibbon segments={ribbonSegments(drawn, axis, ribbonWindow)} label={ribbonLabel} />
            </>
          )}
        </>
      )}
    </div>
  );
}
