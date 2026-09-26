import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { DELAY_THRESHOLDS } from "../../styles/tokens";
import type { HeroLabels } from "./heroCanvas";
import { OVERVIEW } from "./heroMapScene";
import { useHeroMapAnimation } from "./useHeroMapAnimation";

/** The landing hero's animated backdrop: the operations map with trip dots,
 *  the app's right-hand panel, and morphs that carry a trip's reported
 *  stops and the day-playback hours from the map into that panel. Every
 *  element is a real screen of the app; the figures are illustrative
 *  samples, which the canvas itself says. Decorative (`aria-hidden`) — the
 *  headline beside it carries the page's meaning. */
export function LiveMapHero() {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const oneDecimal = (m: number) => (Number.isInteger(m) ? String(m) : m.toFixed(1));
  const labels: HeroLabels = {
    screens: { live: t("landing.hero_map.screens.live"), period: t("landing.hero_map.screens.period") },
    captions: { 0: t("landing.hero_map.captions.live"), 1: t("landing.hero_map.captions.trip"), 2: t("landing.hero_map.captions.playback"), 3: t("landing.hero_map.captions.overview") },
    routes: {
      rapid: t("landing.hero_map.routes.rapid"),
      local: t("landing.hero_map.routes.local"),
      tram3: t("landing.hero_map.routes.tram3"),
      bus12: t("landing.hero_map.routes.bus12"),
      bus7: t("landing.hero_map.routes.bus7"),
      bus3: t("landing.hero_map.routes.bus3"),
    },
    stops: {
      konan: t("landing.hero_map.stops.konan"),
      shiyakusho: t("landing.hero_map.stops.shiyakusho"),
      central: t("landing.hero_map.stops.central"),
      honmachi: t("landing.hero_map.stops.honmachi"),
      higashidai: t("landing.hero_map.stops.higashidai"),
      minatomachi: t("landing.hero_map.stops.minatomachi"),
    },
    queueTitle: t("landing.hero_map.queue_title"),
    kpi: { observed: t("landing.hero_map.kpi.observed"), delayedFivePlus: t("landing.hero_map.kpi.delayed"), onTime: t("landing.hero_map.kpi.on_time") },
    legendBands: [
      t("landing.hero_map.legend.under", { minutes: DELAY_THRESHOLDS.mild }),
      t("landing.hero_map.legend.under", { minutes: DELAY_THRESHOLDS.moderate }),
      t("landing.hero_map.legend.under", { minutes: DELAY_THRESHOLDS.severe }),
      t("landing.hero_map.legend.over", { minutes: DELAY_THRESHOLDS.severe }),
    ],
    legendDisclosure: t("landing.hero_map.legend.disclosure"),
    tripHeading: t("landing.hero_map.trip_heading"),
    tripChartTitle: t("landing.hero_map.trip_chart_title"),
    tripNote: t("landing.hero_map.trip_note"),
    refresh: t("landing.hero_map.refresh"),
    playback: t("landing.hero_map.playback"),
    playbackSpeed: t("landing.hero_map.playback_speed"),
    hourlyTitle: t("landing.hero_map.hourly_title"),
    peak: t("landing.hero_map.peak"),
    overviewAverage: t("landing.hero_map.overview_average"),
    overviewChange: t("landing.hero_map.overview_change", { minutes: Math.abs(OVERVIEW.changeVsPrevious).toFixed(1) }),
    overviewDelayedRoutes: t("landing.hero_map.overview_delayed_routes"),
    routesToCheck: t("landing.hero_map.routes_to_check"),
    sampleNotice: t("landing.hero_map.sample_notice"),
    delayShort: (m) => t("landing.hero_map.delay_short", { minutes: oneDecimal(m) }),
    minutes: (m) => t("landing.hero_map.minutes", { minutes: m.toFixed(1) }),
    hourOfDay: (hour) => t("landing.hero_map.hour_of_day", { hour }),
    clock: (hour) => t("landing.hero_map.clock", { hour: String(hour).padStart(2, "0") }),
    minuteUnit: t("landing.hero_map.minute_unit"),
  };
  useHeroMapAnimation(canvasRef, labels);

  return <canvas ref={canvasRef} className="landing-hero__canvas" aria-hidden="true" />;
}
