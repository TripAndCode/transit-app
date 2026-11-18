import { useTranslation } from "react-i18next";
import { useUrlState } from "../../api/useUrlState";
import { DefinitionMetaBlock } from "../DefinitionMetaBlock";
import { ErrorBanner } from "../ErrorBanner";
import { InlineSparkline } from "../InlineSparkline";
import { SHARED_TABLE, td, th } from "../tableStyles";
import { useRouteReportCard, type Grade, type RouteCard, type RouteReportCardResponse } from "./reportCardApi";
import {
  METRICS,
  formatFigure,
  formatInterval,
  formatMonth,
  gradePoint,
  missingText,
  notGradedText,
  recentMonths,
} from "./reportCardModel";
import "./reportCard.css";

/** Print only the card: the print stylesheet hides the rest of the page while
 *  the root carries this scope, and the scope goes once the dialog closes. */
function printCard() {
  const root = document.documentElement;
  root.dataset.printScope = "report-card";
  window.addEventListener("afterprint", () => delete root.dataset.printScope, { once: true });
  window.print();
}

export function GradeBadge({ grade, large = false }: { grade: Grade | null; large?: boolean }) {
  const { t } = useTranslation();
  const tone = grade === "A" || grade === "B" ? "good" : grade == null ? "none" : "neutral";
  return (
    <span className={`report-grade report-grade--${tone}${large ? " report-grade--large" : ""}`}>
      {grade ?? t("report_card.na_short")}
    </span>
  );
}

/** One route's monthly A to F grade on its page, with the figures behind it,
 *  its last three months and what would raise it, for a closed month the
 *  reader picks (`card_month`, the newest closed month when unset). */
export function RouteReportCard({ agencyId, route }: { agencyId: number; route: string }) {
  const [month, setMonth] = useUrlState<string>("card_month", "");
  const query = useRouteReportCard(agencyId, route, month || null);
  return <RouteReportCardView data={query.data} error={query.error} onRetry={() => void query.refetch()} month={month} onMonth={setMonth} />;
}

export function RouteReportCardView({
  data,
  error,
  onRetry,
  month,
  onMonth,
}: {
  data: RouteReportCardResponse | undefined;
  error: Error | null;
  onRetry: () => void;
  month: string;
  onMonth: (month: string) => void;
}) {
  const { t } = useTranslation();
  // The month the URL asks for wins over a placeholder's, so the picker does
  // not jump back while the new month loads.
  const shown = month || data?.month || "";
  return (
    <section className="report-card" aria-labelledby="report-card-title">
      <div className="report-card__head">
        <h2 id="report-card-title">{t("report_card.title")}</h2>
        <div className="report-card__actions">
          {data && (
            <label>
              <span className="report-card__muted">{t("report_card.month")} </span>
              <select value={shown} onChange={(e) => onMonth(e.target.value)}>
                {recentMonths(data.latest_month).map((m) => (
                  <option key={m} value={m}>{formatMonth(m)}</option>
                ))}
              </select>
            </label>
          )}
          <button type="button" className="btn btn--secondary btn--sm" disabled={!data} onClick={printCard}>
            {t("report_card.print")}
          </button>
        </div>
      </div>
      {error && <ErrorBanner error={error} onRetry={onRetry} />}
      {data && (data.card ? <CardBody data={data} card={data.card} /> : <p className="report-card__muted">{t("report_card.no_card", { month: formatMonth(data.month) })}</p>)}
    </section>
  );
}

function CardBody({ data, card }: { data: RouteReportCardResponse; card: RouteCard }) {
  const { t } = useTranslation();
  const graded = data.history.filter((h): h is typeof h & { grade: Grade } => h.grade != null);
  return (
    <>
      <div className="report-card__verdict">
        <GradeBadge grade={card.grade} large />
        <div>
          <p className="report-card__lead">
            {t("report_card.verdict", { month: formatMonth(card.month), frequency: t(`report_card.frequency.${card.frequency}`) })}
          </p>
          {card.grade == null && <p className="report-card__muted">{notGradedText(t, card)}</p>}
          {card.raise_to.length > 0 && (
            <p className="report-card__raise">
              {t("report_card.raise_to")}{" "}
              {card.raise_to
                .map((r) => t("report_card.raise_item", {
                  metric: t(`report_card.metric.${r.metric}`),
                  value: formatFigure(t, r.metric, r.value),
                  target: formatFigure(t, r.metric, r.target),
                }))
                .join(t("common.list_separator"))}
            </p>
          )}
        </div>
        <div className="report-card__trend" aria-label={t("report_card.trend")}>
          {graded.length >= 2 && <InlineSparkline points={graded.map((h) => gradePoint(h.grade))} width={96} height={28} accent="var(--accent)" forceAccent drawOn={false} />}
          <ol>
            {data.history.map((h) => (
              <li key={h.month}>
                <span className="report-card__muted">{formatMonth(h.month)}</span> <GradeBadge grade={h.grade} />
              </li>
            ))}
          </ol>
        </div>
      </div>
      <table style={SHARED_TABLE}>
        <thead>
          <tr>
            <th style={th({ align: "left" })}>{t("report_card.col.figure")}</th>
            <th style={th({ align: "right" })}>{t("report_card.col.value")}</th>
            <th style={th({ align: "right" })}>{t("report_card.col.interval")}</th>
            <th style={th({ align: "left" })}>{t("report_card.col.letter")}</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map((metric) => {
            const s = card.subscores.find((x) => x.metric === metric);
            if (!s) return null;
            return (
              <tr key={metric} className={s.graded ? undefined : "report-card__ungraded"}>
                <td style={td()}>
                  {t(`report_card.metric.${metric}`)}
                  {!s.graded && <span className="report-card__muted"> {t("report_card.not_graded_on")}</span>}
                </td>
                <td style={td({ align: "right" })}>{s.value == null ? missingText(t, s.missing ?? "not_measured") : formatFigure(t, metric, s.value)}</td>
                <td style={td({ align: "right" })}>{formatInterval(s.ci_low, s.ci_high) ?? t("report_card.no_interval")}</td>
                <td style={td()}>
                  {s.letter == null ? "" : metric === "service_delivered_pct" ? t(`report_card.cap.${s.letter === "A" ? "none" : s.letter}`) : s.letter}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="report-card__muted report-card__rules">
        {t(`report_card.rules.${card.frequency}`)} {t("report_card.rules.truancy", { cap: data.rules.truancy_cap_pct, fail: data.rules.truancy_fail_pct })}
      </p>
      <p className="report-card__muted">
        {t("report_card.stamp", { version: data.definition.method_version })}
      </p>
      <DefinitionMetaBlock definition={data.definition} />
    </>
  );
}
