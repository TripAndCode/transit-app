import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TimeBand } from "../../api/rangeContext";
import type { RouteTrip } from "../../api/types";
import { MOBILE_BREAKPOINT_QUERY, useMediaQuery } from "../../hooks/useMediaQuery";
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

/** Stands in for a value the feed never reported; language-neutral. */
const NO_VALUE = "—";

type Point = { x: number; y: number; delaySec: number };

function tripPoints(trip: RouteTrip, axis: MareyStop[], viewWindow: TimeWindow): Point[] {
  const points: Point[] = [];
  for (const stop of trip.stops) {
    const at = stop.observed_sec ?? stop.scheduled_sec;
    const y = seqToY(stop.stop_sequence, axis, PLOT);
    if (at == null || y == null) continue;
    points.push({ x: timeToX(at, viewWindow, PLOT), y, delaySec: stop.delay_sec });
  }
  return points;
}

/** One trip as text: where it ran and what it had lost by the end. Stop names
 *  live on the route's axis rather than on the trip, so the trip's own stop
 *  sequences are resolved against it. */
type TripRow = { tripId: string; departure: string; firstStop: string; lastStop: string; delayMin: string };

function tripRow(trip: RouteTrip, axis: MareyStop[]): TripRow {
  const departure = tripDeparture(trip);
  const terminal = tripTerminalDelay(trip);
  const names = trip.stops
    .map((stop) => axis.find((rung) => rung.stop_sequence === stop.stop_sequence)?.stop_name)
    .filter((name): name is string => name != null);
  return {
    tripId: trip.trip_id,
    departure: departure == null ? NO_VALUE : formatClock(departure),
    firstStop: names[0] ?? NO_VALUE,
    lastStop: names.length > 0 ? names[names.length - 1] : NO_VALUE,
    delayMin: terminal == null ? NO_VALUE : (terminal / 60).toFixed(1),
  };
}

function tripSummary(trip: RouteTrip, t: (key: string) => string): string {
  const departure = tripDeparture(trip);
  const terminal = tripTerminalDelay(trip);
  return (
    `${t("mareyDeparture")} ${departure == null ? NO_VALUE : formatClock(departure)} · ` +
    `${t("mareyTerminal")} ${terminal == null ? NO_VALUE : (terminal / 60).toFixed(1)} ${t("minutes")}`
  );
}

/** Time–distance (Marey) diagram: stops down the y axis, clock across the x
 *  axis, one polyline per trip. A leg's colour is the delay at the stop it
 *  arrives at, so a line shading from green to red shows exactly where the
 *  journey lost its time.
 *
 *  `role="img"` makes the SVG's subtree presentational, so the same trips are
 *  also published as a table and that table, not the drawing, is what a screen
 *  reader reads. Keyboard users step through the trip groups themselves; what
 *  they hear on each stop is the polite live readout below the chart, which
 *  sits outside the image subtree.
 *
 *  Below the phone breakpoint the diagram is opt-in. A 780-unit viewBox scaled
 *  into a phone-width column renders the axis labels at roughly half their
 *  intended size and closes the gaps between trips, so the table leads there
 *  and the chart is revealed on request inside a horizontal scroller that
 *  keeps it at a legible width.
 */
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
  const [chartRevealed, setChartRevealed] = useState(false);
  const narrow = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  // useId's own value contains colons, which are not usable inside url(#...).
  const clipId = `marey-clip-${useId().replace(/:/g, "")}`;

  const viewWindow = timeWindowForBand(band);
  const drawn = tripsInWindow(trips, viewWindow);
  const ghosts = tripsInWindow(previousTrips, viewWindow);
  const peak = peakWindow(drawn, viewWindow);
  const hoveredTrip = drawn.find((trip) => trip.trip_id === hovered) ?? null;
  const labelStep = Math.max(1, Math.ceil(axis.length / MAX_STOP_LABELS));
  const ribbonWindow = peak ?? viewWindow;
  const ribbonLabel = t("ribbonLabel", {
    from: formatClock(ribbonWindow.startSec),
    to: formatClock(ribbonWindow.endSec),
  });
  const chartShown = !narrow || chartRevealed;
  const rows = drawn.map((trip) => tripRow(trip, axis));

  return (
    <div className="marey">
      <p className="focus-muted marey__caption">
        {t("mareyWindow", { from: formatClock(viewWindow.startSec), to: formatClock(viewWindow.endSec) })} ·{" "}
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
          {narrow && (
            <button
              type="button"
              className="btn-ghost marey__toggle"
              aria-expanded={chartRevealed}
              onClick={() => setChartRevealed((revealed) => !revealed)}
            >
              {chartRevealed ? t("mareyHideChart") : t("mareyShowChart")}
            </button>
          )}
          {chartShown && (
            <div className="marey__scroll">
              <svg
                className="marey__chart"
                viewBox={`0 0 780 ${VIEW_HEIGHT}`}
                role="img"
                aria-label={t("mareyChartLabel", {
                  n: drawn.length,
                  from: formatClock(viewWindow.startSec),
                  to: formatClock(viewWindow.endSec),
                })}
              >
                <defs>
                  <clipPath id={clipId}>
                    <rect x={PLOT.left} y={PLOT.top - 6} width={PLOT.width} height={PLOT.height + 12} />
                  </clipPath>
                </defs>
                {peak && (
                  <rect
                    data-testid="marey-peak"
                    x={Math.max(PLOT.left, timeToX(peak.startSec, viewWindow, PLOT))}
                    y={PLOT.top}
                    width={Math.max(
                      0,
                      Math.min(PLOT.left + PLOT.width, timeToX(peak.endSec, viewWindow, PLOT)) -
                        Math.max(PLOT.left, timeToX(peak.startSec, viewWindow, PLOT)),
                    )}
                    height={PLOT.height}
                    fill="var(--accent-soft)"
                  />
                )}
                {hourTicks(viewWindow).map((sec) => (
                  <g key={sec}>
                    <line
                      x1={timeToX(sec, viewWindow, PLOT)}
                      y1={PLOT.top}
                      x2={timeToX(sec, viewWindow, PLOT)}
                      y2={PLOT.top + PLOT.height}
                      stroke="var(--border-soft)"
                    />
                    <text
                      x={timeToX(sec, viewWindow, PLOT)}
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
                    const points = tripPoints(trip, axis, viewWindow);
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
                  {drawn.map((trip, index) => {
                    const points = tripPoints(trip, axis, viewWindow);
                    const row = rows[index];
                    return (
                      <g
                        key={trip.trip_id}
                        data-trip-id={trip.trip_id}
                        className="marey-trip"
                        tabIndex={0}
                        aria-label={t("mareyTripLabel", {
                          departure: row.departure,
                          first: row.firstStop,
                          last: row.lastStop,
                          delay: row.delayMin,
                        })}
                        opacity={hovered !== null && hovered !== trip.trip_id ? MUTED_OPACITY : 1}
                        onFocus={() => setHovered(trip.trip_id)}
                        onBlur={() => setHovered(null)}
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
            </div>
          )}
          {chartShown && (
            <p className="focus-muted marey__readout" data-testid="marey-readout" aria-live="polite">
              {hoveredTrip ? tripSummary(hoveredTrip, t) : t("mareyHover")}
            </p>
          )}
          <table
            className={`focus-table marey__table${narrow ? "" : " marey__table--hidden"}`}
            data-testid="marey-table"
          >
            <caption>{t("mareyTableCaption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("mareyDeparture")}</th>
                <th scope="col">{t("mareyFirstStop")}</th>
                <th scope="col">{t("mareyLastStop")}</th>
                <th scope="col">
                  {t("mareyTerminal")} ({t("minutes")})
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.tripId}>
                  <td>{row.departure}</td>
                  <td>{row.firstStop}</td>
                  <td>{row.lastStop}</td>
                  <td>{row.delayMin}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
