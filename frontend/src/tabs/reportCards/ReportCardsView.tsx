import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { apiUrl } from "../../api/client";
import { useAgencyId } from "../../api/useAgencyId";
import { useRouteNames } from "../../api/useRouteNames";
import { useUrlState } from "../../api/useUrlState";
import { CsvLink } from "../../components/analysis/CsvLink";
import { DefinitionMetaBlock } from "../../components/DefinitionMetaBlock";
import { ErrorBanner } from "../../components/ErrorBanner";
import { EmptyState } from "../../components/EmptyState";
import { RouteLabel } from "../../components/RouteLabel";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { GradeBadge } from "../../components/reportCard/RouteReportCard";
import { reportCardListPath, useReportCards } from "../../components/reportCard/reportCardApi";
import { METRICS, formatFigure, formatMonth, notGradedText, recentMonths } from "../../components/reportCard/reportCardModel";
import { routeHref } from "../../routes/destinations";
import "../../components/reportCard/reportCard.css";

/** Every route's report card for one closed month: its letter, or why it has
 *  none, beside its figures, each row opening the route's page on its card. */
export function ReportCardsView() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { search } = useLocation();
  const names = useRouteNames(id);
  const [month, setMonth] = useUrlState<string>("card_month", "");
  const query = useReportCards(id, month || null);
  if (id == null) return null;
  const data = query.data;
  return (
    <section className="report-card" aria-labelledby="report-cards-title">
      <div className="report-card__head">
        <h2 id="report-cards-title">{t("report_card.list_title")}</h2>
        <div className="report-card__actions">
          {data && (
            <label>
              <span className="report-card__muted">{t("report_card.month")} </span>
              <select value={month || data.month} onChange={(e) => setMonth(e.target.value)}>
                {recentMonths(data.latest_month).map((m) => (
                  <option key={m} value={m}>{formatMonth(m)}</option>
                ))}
              </select>
            </label>
          )}
          {data && <CsvLink href={apiUrl(reportCardListPath(id, month || data.month, true))} />}
        </div>
      </div>
      {query.error && <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />}
      {data && data.cards.length === 0 && <EmptyState title={t("report_card.list_empty", { month: formatMonth(data.month) })} />}
      {data && data.cards.length > 0 && (
        <div style={{ width: "100%", overflowX: "auto", marginTop: 12 }}>
          {/* While another month loads, the last month's rows stand in, dimmed,
              so they never pass for the month the picker names. */}
          <table style={SHARED_TABLE} className={query.isPlaceholderData ? "report-card__stale" : undefined} aria-busy={query.isPlaceholderData}>
            <thead>
              <tr>
                <th style={th({ align: "left" })}>{t("common.route")}</th>
                <th style={th({ align: "left" })}>{t("report_card.col.frequency")}</th>
                <th style={th({ align: "left" })}>{t("report_card.col.grade")}</th>
                {METRICS.map((metric) => (
                  <th key={metric} style={th({ align: "right" })}>{t(`report_card.metric.${metric}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.cards.map((card) => {
                const params = new URLSearchParams(search);
                params.set("card_month", data.month);
                return (
                  <tr key={card.route_code} style={{ borderTop: "1px solid var(--border-soft)" }}>
                    <td style={td()}>
                      <Link to={routeHref(id, card.route_code, params.toString())}>
                        <RouteLabel code={card.route_code} names={names} compact />
                      </Link>
                    </td>
                    <td style={td()}>{t(`report_card.frequency.${card.frequency}`)}</td>
                    <td style={td()}>
                      <GradeBadge grade={card.grade} />
                      {card.grade == null && <span className="report-card__muted"> {notGradedText(t, card)}</span>}
                    </td>
                    {METRICS.map((metric) => {
                      const s = card.subscores.find((x) => x.metric === metric);
                      return (
                        <td key={metric} style={td({ align: "right" })} className={s?.graded ? undefined : "report-card__muted"}>
                          {formatFigure(t, metric, s?.value ?? null)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {data && <p className="report-card__muted report-card__rules">{t("report_card.rules.list")}</p>}
      {data && <DefinitionMetaBlock definition={data.definition} />}
    </section>
  );
}
