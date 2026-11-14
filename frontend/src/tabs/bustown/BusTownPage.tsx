/**
 * Bus Town — replays one past service day of one agency's buses, gliding
 * between the stops they were actually observed at, coloured by how late
 * they were there. A schematic, not a map, reached only from the command
 * palette rather than the sidebar rail; its own back-link leads out to
 * Pulse, not in.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ArrowLeft, Pause, Play } from "lucide-react";

import { useStopBounds } from "../../api/hooks";
import { useAgencyId } from "../../api/useAgencyId";
import { destHref } from "../../routes/destinations";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { AsyncSection } from "../../components/AsyncSection";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { DelayBandLegend } from "../../components/analysis/StopChart";
import { formatDate, formatMinutes } from "../../utils/format";
import { useBusTownReplay, type BusTownReplay } from "./useBusTownReplay";
import { useBusTownPlayback } from "./useBusTownPlayback";
import { BusTownHourStrip } from "./BusTownHourStrip";
import { boundsFromPoints, formatServiceClock, onTimeCount, positionsAt, scrubberStartHour, slowestRoute } from "./busTownModel";
import "../../styles/scrubber.css";
import "./busTown.css";

/** Simulated seconds that pass per real second, at each speed pill. Framed
 *  as minutes-of-the-day-per-second so a reader can picture the pace
 *  without needing to know the window width in hours. */
const SPEEDS = [60, 180, 360] as const;
const DEFAULT_SPEED_INDEX = 1;

function SpeedPills({ speed, onChange, t }: { speed: number; onChange: (speed: number) => void; t: ReturnType<typeof useTranslation>["t"] }) {
  return (
    <div className="bus-town-speeds" role="group" aria-label={t("busTown.speed_group_aria")}>
      {SPEEDS.map((option) => {
        const minutesPerSecond = option / 60;
        return (
          <button
            key={option}
            type="button"
            className="bus-town-speed-pill"
            aria-pressed={speed === option}
            aria-label={t("busTown.speed_aria", { minutesPerSecond })}
            onClick={() => onChange(option)}
          >
            {t("busTown.speed_label", { minutesPerSecond })}
          </button>
        );
      })}
    </div>
  );
}

function DayPicker({ replay, selectedDate, onChange, t }: {
  replay: BusTownReplay;
  selectedDate: string | null;
  onChange: (date: string) => void;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  if (replay.availableDates.length === 0) return null;
  const value = selectedDate ?? replay.date ?? "";
  return (
    <label className="bus-town-day-picker">
      <span>{t("busTown.day_label")}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {replay.availableDates.map((date) => (
          <option key={date} value={date}>
            {formatDate(date, { year: false, weekday: true })}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * A thin wrapper so `key={agencyId}` remounts the stateful inner page on an
 * agency switch — `useAgencyId` is reactive but the same route element, so
 * without a remount the previous agency's picked date, playhead and speed
 * would otherwise survive into the new agency's replay request.
 */
export function BusTownPage() {
  const agencyId = useAgencyId();
  return <BusTownPageForAgency key={agencyId ?? "none"} agencyId={agencyId} />;
}

function BusTownPageForAgency({ agencyId }: { agencyId: number | null }) {
  const { t } = useTranslation();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [speed, setSpeed] = useState<number>(SPEEDS[DEFAULT_SPEED_INDEX]);
  const [sec, setSec] = useState(scrubberStartHour(null) * 3600);

  const replay = useBusTownReplay(agencyId, selectedDate, sec, speed);
  const stopBoundsQuery = useStopBounds(agencyId);
  const bounds = stopBoundsQuery.data ?? boundsFromPoints(replay.stopPoints);
  const playhead = replay.playhead;

  const playback = useBusTownPlayback({
    model: replay.model,
    stopPoints: replay.stopPoints,
    stopPairs: replay.stopPairs,
    bounds,
    minSec: replay.minSec,
    maxSec: replay.maxSec,
    speed,
    autoplay: !reducedMotion,
    sec: playhead,
    setSec,
  });

  const buses = positionsAt(replay.model, playhead);
  const onTime = onTimeCount(buses);
  const slowest = slowestRoute(replay.trips, replay.routeNames);

  const data = replay.loaded ? replay : undefined;

  return (
    <div className="bus-town-page">
      <PageHeader title={t("nav.bus-town")} subtitle={t("busTown.subtitle")} />
      {agencyId != null && (
        <Link to={destHref(agencyId, "pulse")} className="bus-town-back-link">
          <ArrowLeft size={14} aria-hidden="true" /> {t("busTown.back_to_pulse")}
        </Link>
      )}
      <DayPicker replay={replay} selectedDate={selectedDate} onChange={setSelectedDate} t={t} />

      <AsyncSection
        loading={replay.loading}
        error={replay.error}
        data={data}
        hasContent={(d) => d.model.size > 0}
        empty={<EmptyState title={t("busTown.empty_title")} hint={t("busTown.empty_hint")} />}
      >
        {() => (
          <div className="bus-town-layout">
            <div className="bus-town-stage">
              <div className="bus-town-canvas-frame">
                <canvas ref={playback.canvasRef} className="bus-town-canvas" role="img" aria-label={t("busTown.canvas_aria")} />
                <span className="bus-town-clock num" aria-hidden="true">
                  {formatServiceClock(playhead)}
                </span>
              </div>
              <div className="bus-town-controls">
                <button
                  type="button"
                  className="bus-town-play-button"
                  aria-pressed={playback.playing}
                  aria-label={playback.playing ? t("busTown.pause") : t("busTown.play")}
                  onClick={() => (playback.playing ? playback.pause() : playback.play())}
                >
                  {playback.playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
                </button>
                <div className="bus-town-scrub">
                  <input
                    type="range"
                    className="scrub-input"
                    min={replay.minSec}
                    max={replay.maxSec}
                    step={60}
                    value={playhead}
                    aria-label={t("busTown.scrubber_aria")}
                    aria-valuetext={formatServiceClock(playhead)}
                    onChange={(event) => playback.seek(Number(event.target.value))}
                  />
                  <BusTownHourStrip
                    hours={replay.departuresByHour}
                    startHour={replay.minSec / 3600}
                    endHour={replay.maxSec / 3600}
                    playheadSec={playhead}
                    t={t}
                  />
                </div>
                <SpeedPills speed={speed} onChange={setSpeed} t={t} />
              </div>
              <div className="bus-town-legend">
                <DelayBandLegend />
              </div>
              <p className="bus-town-estimate-note">{t("busTown.estimate_note")}</p>
            </div>

            <div className="bus-town-cards">
              <Card className="bus-town-card">
                <h2>{t("busTown.on_time_title")}</h2>
                {onTime.total === 0 ? (
                  <p>{t("busTown.on_time_empty")}</p>
                ) : (
                  <p className="num">{t("busTown.on_time_value", { onTime: onTime.onTime, total: onTime.total })}</p>
                )}
              </Card>
              <Card className="bus-town-card">
                <h2>{t("busTown.slowest_route_title")}</h2>
                {slowest == null ? (
                  <p>{t("busTown.slowest_route_empty")}</p>
                ) : (
                  <p>{t("busTown.slowest_route_value", { route: slowest.name, delay: formatMinutes(slowest.meanDelayMin) })}</p>
                )}
              </Card>
              <Card className="bus-town-card">
                <h2>{t("busTown.how_it_works_title")}</h2>
                <p>{t("busTown.how_it_works_body")}</p>
              </Card>
            </div>
          </div>
        )}
      </AsyncSection>
    </div>
  );
}
