import { useTranslation } from "react-i18next";
import { REPORT, REPORT_ROUTES, ON_TIME_THRESHOLD_SECONDS } from "./welcomeSample";
import { contentLocale } from "../../i18n/contentLocale";
import { fixedTenth, formatNumber } from "../../utils/format";

const CHECK_KEYS = ["landing.councils.check_rates", "landing.councils.check_unobserved", "landing.councils.check_print"] as const;

// Matches each route bar row's own name/value gutters, so the dashed target
// line (positioned once, across every row) lines up with the bars under it.
const NAME_COL = "92px";
const VALUE_COL = "40px";

export function CouncilSample() {
  const { t, i18n } = useTranslation();
  const belowTargetNames = REPORT_ROUTES.filter((r) => r.belowTarget).map((r) => t(`landing.report.routes.${r.nameKey}`));
  const separator = contentLocale(i18n) === "ja" ? "・" : ", "; // i18n-ignore: locale-neutral separator
  const period = t("landing.report.period", {
    year: REPORT.fiscalYear,
    quarter: REPORT.quarter,
    from: `${REPORT.fromMonth}/${REPORT.fromDay}`,
    to: `${REPORT.toMonth}/${REPORT.toDay}`,
    routes: REPORT.routeCount,
  });
  return (
    <section id="councils" className="landing-section landing-section--band">
      <div className="landing-councils">
        <div className="landing-councils__text">
          <div className="landing-eyebrow">{t("landing.councils.eyebrow")}</div>
          <h2 className="landing-h2">{t("landing.councils.heading")}</h2>
          <p className="landing-lead">{t("landing.councils.lead")}</p>
          <div className="landing-checks">
            {CHECK_KEYS.map((key) => (
              <span className="landing-check" key={key}>
                {t(key)}
              </span>
            ))}
          </div>
          <span className="landing-door__chips landing-councils__chips">
            <span className="landing-chip">{t("landing.chips.pdf")}</span>
            <span className="landing-chip">{t("landing.chips.csv")}</span>
            <span className="landing-chip">{t("landing.chips.print")}</span>
          </span>
          <p className="landing-muted landing-councils__disclaimer">{t("landing.councils.disclaimer")}</p>
        </div>
        <div className="landing-report-figure">
          <div className="landing-report-figure__backdrop" aria-hidden="true" />
          <article className="landing-report" aria-label={t("landing.report.aria_label")}>
            <div className="landing-report__head">
              <span>{t("header.app_tagline")}</span>
              <span className="landing-report__badge">{t("landing.report.badge")}</span>
            </div>
            <div className="landing-muted">{t("landing.report.addressee")}</div>
            <div className="landing-report__title">{t("landing.report.title")}</div>
            <div className="landing-muted">{period}</div>
            <div className="landing-report__kpis">
              <div className="landing-report__kpi">
                <div className="landing-muted">{t("landing.report.kpi_confirmed_label")}</div>
                <div className="landing-report__kpi-value">{fixedTenth(REPORT.confirmationPct)}%</div>
                <div className="landing-muted">
                  {t("landing.report.kpi_confirmed_sub", { confirmed: formatNumber(REPORT.confirmedTrips), total: formatNumber(REPORT.totalTrips) })}
                </div>
              </div>
              <div className="landing-report__kpi">
                <div className="landing-muted">{t("landing.report.kpi_ontime_label")}</div>
                <div className="landing-report__kpi-value">{fixedTenth(REPORT.onTimePct)}%</div>
                <div className="landing-muted">{t("landing.report.kpi_ontime_sub", { seconds: ON_TIME_THRESHOLD_SECONDS })}</div>
              </div>
              <div className="landing-report__kpi">
                <div className="landing-muted">{t("landing.report.kpi_coverage_label")}</div>
                <div className="landing-report__kpi-value">{fixedTenth(REPORT.coveragePct)}%</div>
                <div className="landing-muted">{t("landing.report.kpi_coverage_sub")}</div>
              </div>
            </div>
            <div className="landing-report__bars-head">
              <b>{t("landing.report.route_bars_heading")}</b>
              <span className="landing-muted">{t("landing.report.target_note", { pct: REPORT.targetPct })}</span>
            </div>
            <div className="landing-report__bars">
              <span
                aria-hidden="true"
                className="landing-report__target-line"
                style={{ left: `calc(${NAME_COL} + (100% - ${NAME_COL} - ${VALUE_COL}) * ${REPORT.targetPct}/100)` }}
              />
              {REPORT_ROUTES.map((route) => (
                <div className="landing-report__bar-row" key={route.nameKey} style={{ gridTemplateColumns: `${NAME_COL} 1fr ${VALUE_COL}` }}>
                  <span>{t(`landing.report.routes.${route.nameKey}`)}</span>
                  <i className={route.belowTarget ? "landing-report__bar landing-report__bar--below" : "landing-report__bar"} style={{ width: `${route.pct}%` }} />
                  <span>{fixedTenth(route.pct)}</span>
                </div>
              ))}
            </div>
            {belowTargetNames.length > 0 && (
              <div className="landing-report__below-target">
                {t("landing.report.below_target", { routes: belowTargetNames.join(separator) })}
              </div>
            )}
            <p className="landing-report__footnote">
              {t("landing.report.footnote", {
                seconds: ON_TIME_THRESHOLD_SECONDS,
                timetableDate: `${REPORT.timetableYear}-${String(REPORT.timetableMonth).padStart(2, "0")}-${String(REPORT.timetableDay).padStart(2, "0")}`,
                version: REPORT.definitionVersion,
                unobserved: formatNumber(REPORT.unobservedTrips),
              })}
            </p>
            <div className="landing-report__foot">
              <span>{t("landing.report.generated_line", { date: REPORT.generatedDate, id: REPORT.recalculationId })}</span>
              <span>{t("landing.report.page_indicator", { page: REPORT.page, total: REPORT.pageTotal })}</span>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
