import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCtaKind } from "./useCtaKind";
import { busTownHref } from "./busTownLink";
import { readLastAgency } from "../../api/lastAgency";
import { BUS_TOWN_SAMPLE } from "./welcomeSample";
import { fixtureFill } from "./severityStyle";
import { SampleTag } from "./SampleTag";
import type { FixtureBand } from "./severityStyle";

const ROUTES = [
  { key: "r1", d: "M30 230 H200 Q230 230 230 200 V120 Q230 90 260 90 H700", stroke: "#5B7BFF" },
  { key: "r2", d: "M100 40 V140 Q100 170 130 170 H520 Q550 170 550 200 V280", stroke: "#22B8C6" },
  { key: "r3", d: "M400 30 V280", stroke: "#9A80FF" },
] as const;

// Each marker's offset-distance is its static, reduced-motion position; the
// "drive" keyframe (scoped to prefers-reduced-motion: no-preference) takes
// over from there and ignores it once animation starts, matching the
// mockup's own HTML "pill" markers riding a CSS motion path rather than
// moving SVG shapes (wider browser support for offset-path).
const MARKERS: ReadonlyArray<{ route: 0 | 1 | 2; band: FixtureBand; offsetDistance: string; duration: string; delay: string }> = [
  { route: 0, band: "ok", offsetDistance: "12%", duration: "16s", delay: "-2s" },
  { route: 0, band: "mild", offsetDistance: "70%", duration: "19s", delay: "-11s" },
  { route: 1, band: "ok", offsetDistance: "30%", duration: "15s", delay: "-5s" },
  { route: 1, band: "severe", offsetDistance: "80%", duration: "21s", delay: "-9s" },
  { route: 2, band: "ok", offsetDistance: "20%", duration: "12s", delay: "-3s" },
  { route: 2, band: "moderate", offsetDistance: "75%", duration: "14s", delay: "-10s" },
];

const STOPS = [
  { x: 30, y: 230 },
  { x: 700, y: 90 },
  { x: 100, y: 40 },
  { x: 550, y: 280 },
  { x: 400, y: 30 },
  { x: 400, y: 280 },
] as const;

/** The one real, non-decorative CTA on this page: a visitor who would reach
 *  the signed-in app goes straight into Bus Town for their last-chosen
 *  agency (or "/" to pick one); anyone else goes to sign-in first. The mini
 *  route map is purely illustrative playback chrome, presentational via the
 *  SVG's own role="img". */
export function BusTownTeaser() {
  const { t } = useTranslation();
  const { kind } = useCtaKind();
  const reachesApp = kind !== "signed_out";
  const href = busTownHref(reachesApp, readLastAgency());
  return (
    <section id="bus-town" className="landing-section landing-section--dark">
      <div className="landing-stage landing-bustown">
        <div className="landing-bustown__text">
          <div className="landing-eyebrow landing-bustown__eyebrow">{t("landing.bustown.eyebrow")}</div>
          <h2 className="landing-h2 landing-bustown__heading">{t("landing.bustown.heading")}</h2>
          <p className="landing-bustown__body">{t("landing.bustown.body")}</p>
          <Link className="btn btn--primary landing-bustown__cta" to={href}>
            {t("landing.bustown.cta", { name: t("nav.bus-town") })}
          </Link>
        </div>
        <div className="landing-bustown__panel">
          <div className="landing-bustown__map">
            <svg width="720" height="310" viewBox="0 0 720 310" role="img" aria-label={t("landing.bustown.map_alt")}>
              <path d="M0 50H720M0 130H720M0 210H720M0 290H720M80 0V310M240 0V310M400 0V310M560 0V310" stroke="#17213D" />
              <g fill="none" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round">
                {ROUTES.map((route) => (
                  <path key={route.key} d={route.d} stroke={route.stroke} />
                ))}
              </g>
              <g fill="#101830" stroke="#E8ECF6" strokeWidth="2.5">
                {STOPS.map((stop, i) => (
                  <circle key={i} cx={stop.x} cy={stop.y} r="6" />
                ))}
              </g>
            </svg>
            {MARKERS.map((marker, i) => (
              <span
                key={i}
                aria-hidden="true"
                className="landing-bustown__marker"
                style={{
                  background: fixtureFill(marker.band),
                  offsetPath: `path('${ROUTES[marker.route].d}')`,
                  offsetDistance: marker.offsetDistance,
                  animationDuration: marker.duration,
                  animationDelay: marker.delay,
                }}
              />
            ))}
          </div>
          <div className="landing-bustown__caption">
            {t("landing.bustown.caption", { city: t(`landing.observatory.rows.${BUS_TOWN_SAMPLE.cityKey}`), date: BUS_TOWN_SAMPLE.date })}
            {" · "}
            <SampleTag />
          </div>
        </div>
      </div>
    </section>
  );
}
