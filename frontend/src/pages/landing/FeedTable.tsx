import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { FEED_ROWS, STATUS, ON_TIME_THRESHOLD_SECONDS, onTimeRateBand } from "./welcomeSample";
import { fixtureFill } from "./severityStyle";
import { SampleTag } from "./SampleTag";
import { fixedTenth } from "../../utils/format";

function formatFreshness(seconds: number | null, t: TFunction): string {
  if (seconds == null) return t("landing.observatory.offline");
  if (seconds < 60) return t("scope.duration_sec", { n: seconds });
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest === 0
    ? t("scope.duration_min", { n: minutes })
    : `${t("scope.duration_min", { n: minutes })} ${t("scope.duration_sec", { n: rest })}`;
}

function formatAgo(seconds: number, t: TFunction): string {
  return seconds < 60
    ? t("landing.observatory.ago_seconds", { n: seconds })
    : t("landing.observatory.ago_minutes", { n: Math.round(seconds / 60) });
}

/** The OBSERVATORY section: a sample grading table for a handful of the
 *  (fixture) feeds the stage's status bar counts. Built with ARIA grid
 *  roles rather than a native `<table>` so narrow widths can collapse rows
 *  into a card layout in CSS without changing the semantics. */
export function FeedTable() {
  const { t } = useTranslation();
  return (
    <section id="observatory" className="landing-section">
      <div className="landing-section__head">
        <div>
          <div className="landing-eyebrow">{t("landing.observatory.eyebrow")}</div>
          <h2 className="landing-h2">{t("landing.observatory.heading")}</h2>
          <p className="landing-lead">{t("landing.observatory.lead")}</p>
        </div>
        <div className="landing-section__head-meta">
          <SampleTag />
          <span className="landing-muted">{t("landing.observatory.shown_count", { shown: FEED_ROWS.length, total: STATUS.feeds })}</span>
        </div>
      </div>
      <div className="landing-card landing-table-wrap">
        <div role="table" aria-label={t("landing.observatory.table_aria")} className="landing-table">
          <div className="landing-table__row landing-table__row--head" role="row">
            <span role="columnheader">{t("landing.observatory.col_city")}</span>
            <span role="columnheader">{t("landing.observatory.col_freshness")}</span>
            <span role="columnheader">{t("landing.observatory.col_ontime")}</span>
            <span role="columnheader">{t("landing.observatory.col_grade")}</span>
            <span role="columnheader">{t("landing.observatory.col_updated")}</span>
          </div>
          {FEED_ROWS.map((row) => {
            const onTimeBand = row.onTimePct == null ? null : onTimeRateBand(row.onTimePct);
            return (
              <div className="landing-table__row" role="row" key={row.cityKey}>
                <span role="cell" className="landing-table__city">
                  <b>{t(`landing.observatory.rows.${row.cityKey}`)}</b>
                  <span className="landing-muted">{t("landing.observatory.feed_count", { n: row.feeds })}</span>
                </span>
                <span role="cell" className="landing-table__freshness">
                  <span className="landing-swatch" style={{ background: fixtureFill(row.freshnessBand) }} />
                  {formatFreshness(row.freshnessSeconds, t)}
                </span>
                <span role="cell" className="landing-table__ontime">
                  {row.onTimePct == null ? (
                    <span className="landing-muted">{t("landing.observatory.no_data")}</span>
                  ) : (
                    <>
                      <span className="landing-table__ontime-value">{fixedTenth(row.onTimePct)}%</span>
                      <span className="landing-bar">
                        <span className="landing-bar__fill" style={{ width: `${row.onTimePct}%`, background: fixtureFill(onTimeBand!) }} />
                      </span>
                    </>
                  )}
                </span>
                <span role="cell" className="landing-table__grade">
                  <span className={`landing-grade landing-grade--${row.gradeBand}`}>{row.grade}</span>
                  <span className="landing-muted">{t("landing.observatory.coverage_label", { pct: row.coveragePct })}</span>
                </span>
                <span role="cell" className="landing-muted">
                  {formatAgo(row.lastUpdateSeconds, t)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="landing-section__foot">
        <span>{t("landing.observatory.footnote", { seconds: ON_TIME_THRESHOLD_SECONDS })}</span>
        <a href="#method" className="landing-section__foot-link">
          {t("landing.observatory.see_all_link", { total: STATUS.feeds })} →
        </a>
      </div>
    </section>
  );
}
