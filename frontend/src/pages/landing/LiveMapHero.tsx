import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { HeroLabels } from "./heroMapDraw";
import { useHeroMapAnimation } from "./useHeroMapAnimation";

/** The landing hero's animated backdrop: a fictional night-time city map on
 *  which routes tint by per-section delay and every stop grows a tower as
 *  tall as its average delay. Decorative (`aria-hidden`) — the headline next
 *  to it carries the page's meaning; the figures are illustrative samples,
 *  which the canvas itself labels. */
export function LiveMapHero() {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const delay = (minutes: number) => t("landing.hero_map.delay", { minutes: minutes.toFixed(1) });
  const labels: HeroLabels = {
    stations: {
      central: t("landing.hero_map.stations.central"),
      harbor: t("landing.hero_map.stations.harbor"),
      west: t("landing.hero_map.stations.west"),
      eastHill: t("landing.hero_map.stations.east_hill"),
      north: t("landing.hero_map.stations.north"),
      seaside: t("landing.hero_map.stations.seaside"),
    },
    districts: {
      north: t("landing.hero_map.districts.north"),
      riverside: t("landing.hero_map.districts.riverside"),
      central: t("landing.hero_map.districts.central"),
      port: t("landing.hero_map.districts.port"),
      east: t("landing.hero_map.districts.east"),
    },
    captions: {
      0: { title: t("landing.hero_map.captions.live.title"), body: t("landing.hero_map.captions.live.body") },
      1: { title: t("landing.hero_map.captions.sections.title"), body: t("landing.hero_map.captions.sections.body") },
      2: { title: t("landing.hero_map.captions.towers.title"), body: t("landing.hero_map.captions.towers.body") },
    },
    calloutRoute: t("landing.hero_map.callout_route"),
    calloutWeekOverWeek: (minutes) => t("landing.hero_map.callout_week_over_week", { minutes: minutes.toFixed(1) }),
    towerLabel: t("landing.hero_map.tower_label"),
    legendOnTime: t("landing.hero_map.legend_on_time"),
    legendDelayed: t("landing.hero_map.legend_delayed"),
    hud: [t("landing.hero_map.hud_title"), t("landing.hero_map.hud_scope"), t("landing.hero_map.hud_sample")],
    delay,
  };
  useHeroMapAnimation(canvasRef, labels);

  return <canvas ref={canvasRef} className="landing-hero__canvas" aria-hidden="true" />;
}
