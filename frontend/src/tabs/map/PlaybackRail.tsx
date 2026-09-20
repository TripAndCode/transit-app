import { useEffect, useEffectEvent, useRef } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import type { PlaybackController } from "./useDayPlayback";

/**
 * The day-playback transport, pinned to the bottom of the operations map.
 *
 * It is a transport, not a chart: the load bar behind the scrubber colours each
 * frame by its own mean delay, so the shape of the day (a green morning, an
 * amber peak) is legible before anything is played, and the scrubber lands on
 * the hour the reader can already see is interesting.
 *
 * Keyboard control is delegated from the rail's own element rather than bound
 * per control: the arrow keys have to mean "step the playhead" wherever focus
 * sits inside the rail, including on the scrubber, whose native arrow handling
 * would otherwise move the value without wrapping at either end.
 */
export function PlaybackRail({
  controller,
  onExit,
  t,
}: {
  controller: PlaybackController;
  onExit: () => void;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const railRef = useRef<HTMLDivElement>(null);
  const { frames, index, playing, speed, steppingOnly, loading, date } = controller;
  const frame = frames[index];

  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      controller.step(event.key === "ArrowRight" ? 1 : -1);
      return;
    }
    if (event.key !== " " && event.key !== "Spacebar") return;
    // A focused button already answers Space by activating itself; handling it
    // here too would toggle playback twice for one press.
    if (event.target instanceof HTMLButtonElement) return;
    if (steppingOnly) return;
    event.preventDefault();
    controller.toggle();
  });

  useEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    const handler = (event: KeyboardEvent) => onKeyDown(event);
    rail.addEventListener("keydown", handler);
    return () => rail.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="ops-playback" ref={railRef} role="group" aria-label={t("operations.playback.aria_label")}>
      <div className="ops-playback__transport">
        {steppingOnly ? (
          <>
            <button
              type="button"
              className="ops-playback__button"
              aria-label={t("operations.playback.step_back")}
              onClick={() => controller.step(-1)}
              disabled={frames.length === 0}
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="ops-playback__button"
              aria-label={t("operations.playback.step_forward")}
              onClick={() => controller.step(1)}
              disabled={frames.length === 0}
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </>
        ) : (
          <button
            type="button"
            className="ops-playback__button ops-playback__button--primary"
            aria-label={playing ? t("operations.playback.pause") : t("operations.playback.play")}
            aria-pressed={playing}
            onClick={controller.toggle}
            disabled={frames.length === 0}
          >
            {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          </button>
        )}
      </div>

      {loading || frames.length === 0 ? (
        <p className="ops-playback__status" role="status">
          {loading ? t("operations.playback.loading") : t("operations.playback.empty")}
        </p>
      ) : (
        <div className="ops-playback__track">
          {/* Decoration for the scrubber that sits on top of it: the reader
              gets the same information from the clock and the map. */}
          <div className="ops-playback__load" aria-hidden="true">
            {frames.map((f) => (
              <span
                key={f.t}
                data-testid="playback-load-segment"
                style={{
                  background: f.mean_delay_min == null ? "var(--track-bg)" : delayColor(f.mean_delay_min),
                }}
              />
            ))}
          </div>
          <input
            type="range"
            className="ops-playback__scrub"
            min={0}
            max={frames.length - 1}
            step={1}
            value={index}
            aria-label={t("operations.playback.scrubber")}
            aria-valuetext={frame?.t ?? ""}
            onChange={(event) => controller.setIndex(Number(event.target.value))}
          />
        </div>
      )}

      <p className="ops-playback__clock num">
        <b>{frame?.t ?? "--:--"}</b>
        <small>{date ?? ""}</small>
      </p>

      <button
        type="button"
        className="ops-playback__button ops-playback__speed num"
        aria-label={t("operations.playback.speed", { speed })}
        onClick={controller.cycleSpeed}
        disabled={steppingOnly}
      >
        {t("operations.playback.speed_label", { speed })}
      </button>

      <button
        type="button"
        className="ops-playback__button"
        aria-label={t("operations.playback.exit")}
        onClick={onExit}
      >
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
