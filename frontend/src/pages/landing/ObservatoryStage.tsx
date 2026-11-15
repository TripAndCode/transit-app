import { useTranslation } from "react-i18next";
import { STAGE_CITIES, STATUS } from "./welcomeSample";
import { fixtureFill, fixtureRing } from "./severityStyle";

// The coastline this traces is decorative chrome (the hero's dark "stage"
// background), not information -- it carries no label and no semantic role
// of its own, only the city dots on top of it do.
const COASTLINE_D =
  "M460 28h8.01M460 36h16.01M460 44h24.01M460 52h32.01M460 60h40.01M460 68h56.01M540 68h8.01M460 76h88.01M452 84h96.01M452 92h104.01M428 100h104.01M420 108h96.01M412 116h32.01M460 116h40.01M412 124h8.01M476 124h24.01M412 132h8.01M492 132h0.01M420 140h16.01M412 148h8.01M444 156h0.01M84 164h8.01M420 164h0.01M444 164h0.01M76 172h8.01M420 172h24.01M76 180h8.01M412 180h40.01M68 188h8.01M412 188h40.01M68 196h8.01M412 196h40.01M68 204h0.01M404 204h56.01M412 212h48.01M412 220h48.01M412 228h48.01M412 236h40.01M404 244h48.01M404 252h48.01M404 260h32.01M388 268h48.01M380 276h56.01M340 284h0.01M380 284h56.01M332 292h0.01M372 292h56.01M324 300h8.01M356 300h72.01M324 308h104.01M324 316h104.01M316 324h112.01M308 332h120.01M308 340h120.01M236 348h8.01M260 348h168.01M212 356h208.01M204 364h176.01M412 364h0.01M204 372h112.01M332 372h32.01M380 372h0.01M188 380h56.01M292 380h24.01M172 388h32.01M252 388h0.01M284 388h40.01M172 396h24.01M244 396h16.01M284 396h24.01M164 404h8.01M220 404h48.01M292 404h8.01M156 412h32.01M212 412h48.01M300 412h0.01M140 420h40.01M204 420h24.01M252 420h0.01M140 428h48.01M220 428h8.01M140 436h0.01M164 436h24.01M220 436h0.01M164 444h24.01M164 452h16.01M156 460h24.01M156 468h24.01M156 476h24.01M156 484h24.01M164 492h0.01";

const LEGEND_BANDS = [
  { band: "ok", key: "landing.stage.legend_band_high" },
  { band: "mild", key: "landing.stage.legend_band_mid" },
  { band: "moderate", key: "landing.stage.legend_band_low" },
  { band: "severe", key: "landing.stage.legend_band_poor" },
] as const;

/** The hero's dark, always-on "observatory" stage: a dotted map of Japan
 *  whose points gently breathe in the band color of the (fixture) city they
 *  stand for. Purely illustrative -- the real figures sit beside it in
 *  StatusBar -- so every dot but the 7 labelled cities is `aria-hidden`. */
export function ObservatoryStage() {
  const { t } = useTranslation();
  return (
    <div className="landing-stage">
      <svg
        className="landing-stage__bg"
        viewBox="0 0 600 520"
        role="img"
        aria-label={t("landing.stage.map_alt")}
      >
        <defs>
          <pattern id="landing-stage-sea" width="8" height="8" patternUnits="userSpaceOnUse">
            <circle cx="4" cy="4" r=".9" fill="#19223F" />
          </pattern>
        </defs>
        <rect width="600" height="520" fill="url(#landing-stage-sea)" />
        <path d={COASTLINE_D} fill="none" stroke="#43568A" strokeWidth="4.6" strokeLinecap="round" strokeDasharray="0 8" />
        <rect x="36" y="134" width="90" height="92" rx="8" fill="none" stroke="#2A3760" strokeWidth="1.5" />
      </svg>
      <span className="landing-stage__okinawa-label">{t("landing.stage.okinawa_inset")}</span>
      <div className="landing-stage__dots">
        {STAGE_CITIES.map((city, i) => (
          <div key={city.key} className="landing-stage__dot" style={{ left: `${city.x}%`, top: `${city.y}%` }}>
            <span
              aria-hidden="true"
              className="landing-stage__halo"
              style={{
                background: fixtureFill(city.band),
                animationDelay: `${(-((i * 0.53) % 4.4)).toFixed(2)}s`,
                animationDuration: `${(4 + (i % 4) * 0.35).toFixed(2)}s`,
              }}
            />
            <span aria-hidden="true" className="landing-stage__core" style={{ background: fixtureFill(city.band), boxShadow: fixtureRing(city.band) }} />
            {city.labelKey && (
              <span className={`landing-stage__label landing-stage__label--${city.labelSide}`}>
                {t(`landing.stage.cities.${city.labelKey}`)}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="landing-stage__title">
        <div className="landing-stage__title-main">{t("landing.stage.title")}</div>
        <div className="landing-stage__title-sub">{t("landing.stage.subtitle", { feeds: STATUS.feeds, cities: STATUS.cities })}</div>
      </div>
      <div className="landing-stage__legend">
        <b>{t("landing.stage.legend_title")}</b>
        {LEGEND_BANDS.map(({ band, key }) => (
          <span className="landing-stage__legend-row" key={band}>
            <span className="landing-stage__swatch" style={{ background: fixtureFill(band) }} />
            {t(key)}
          </span>
        ))}
        <span className="landing-stage__legend-row">
          <span className="landing-stage__swatch landing-stage__swatch--offline" />
          {t("landing.stage.legend_offline")}
        </span>
      </div>
    </div>
  );
}
