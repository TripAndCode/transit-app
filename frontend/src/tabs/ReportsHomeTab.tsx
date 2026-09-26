import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgencies, useReport } from "../api/hooks";
import { useJumpToLatestDataRange } from "../api/defaultRangeAnchor";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { useUrlState } from "../api/useUrlState";
import { useRouteNames } from "../api/useRouteNames";
import { useAgencyId } from "../api/useAgencyId";
import type { RankingRow, TrendDay } from "../api/types";
import { TabFilterBar } from "../components/TabFilterBar";
import { buildCsv, downloadCsv, type CsvColumn } from "../components/analysis/csv";
import { ExportMenu } from "../components/ExportMenu";
import { deleteAnalysis, readAnalyses } from "../components/analysis/savedAnalyses";
import { PeriodChart } from "../components/analysis/PeriodChart";
import { AsyncSection } from "../components/AsyncSection";
import { EmptyState } from "../components/EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { FILTER_SEPARATOR } from "../utils/format";
import { SHARED_TABLE, td, th } from "../components/tableStyles";
import "../styles/focusedAnalysis.css";

const daysColumns: CsvColumn<TrendDay>[] = [
  { header: "date", value: (d) => d.date },
  { header: "mean_departure_delay_minutes", value: (d) => d.avg_min },
  { header: "observations", value: (d) => d.samples },
];

const rankingColumns: CsvColumn<RankingRow>[] = [
  { header: "route_code", value: (r) => r[0] },
  { header: "service_type", value: (r) => r[1] },
  { header: "mean_minutes", value: (r) => r[2] },
  { header: "median_minutes", value: (r) => r[3] },
  { header: "p90_minutes", value: (r) => r[4] },
  { header: "observations", value: (r) => r[5] },
];

export function ReportsHomeTab() {
  const id = useAgencyId();
  const { t } = useTranslation("design");
  const [ctx, update] = useRangeContext();
  const jumpToLatestData = useJumpToLatestDataRange(id);
  const [view, setView] = useUrlState<"summary" | "saved">("view", "summary");
  const savedTab = view === "saved";
  const trend = useReport(id, savedTab ? null : "trend", ctx);
  const ranking = useReport(id, savedTab ? null : "ranking", ctx);
  const agencies = useAgencies();
  const names = useRouteNames(id);
  const [saved, setSaved] = useState(readAnalyses);
  const [notice, setNotice] = useState("");
  // Both queries pin their own report_type above, so these narrowings can
  // only fall through while the response for that type is still in flight.
  const trendPayload = trend.data?.report_type === "trend" ? trend.data.rows[0] : undefined;
  const days = (trendPayload?.days ?? []).filter((d) => Number.isFinite(d.avg_min) && d.samples > 0).sort((a, b) => a.date.localeCompare(b.date));
  const rows: RankingRow[] = ranking.data?.report_type === "ranking" ? ranking.data.rows : [];
  const queryString = ctxToQueryString(ctx);
  const chartWrapRef = useRef<HTMLDivElement>(null);
  // Shared by both the trend and ranking EmptyStates below -- same ctx, same
  // way out either way.
  const emptyReasons = buildFilterCtxReasons(ctx, t);
  const emptyRecoveries = buildFilterCtxRecoveries({
    ctx,
    onClearRoutes: () => update({ routes: null }),
    onResetService: () => update({ service: "all" }),
    jumpToLatestData,
    t,
  });
  return <div className="focus-page">
    <header className="focus-header"><div><h1>{t("reports")}</h1><p>{t("reportTitle")}</p></div>
      {!savedTab && <ExportMenu
        svgContainerRef={chartWrapRef}
        pngFilenameBase={`trend-${id}`}
        csv={{
          filenameBase: `report-${id}-${ctx.from}-${ctx.to}`,
          rows: days,
          columns: daysColumns,
          ctx,
          extraRows: [[], ...buildCsv(rows, rankingColumns)],
        }}
      />}
    </header>
    <div className="focus-tabs">{(["summary", "saved"] as const).map((v) => <button key={v} aria-pressed={view === v} onClick={() => { setSaved(readAnalyses()); setView(v); }}>{t(v === "saved" ? "savedAnalyses" : "summary")}</button>)}</div>
    {notice && <p role="status">{notice}</p>}
    {savedTab ? <section><p className="focus-muted">{t("localOnly")}</p>
      {!saved.some((s) => s.agencyId === id) && <EmptyState title={t("noSaved")} />}
      <ul className="focus-saved">{saved.filter((s) => s.agencyId === id).map((s) => <li key={s.id}>
        <Link to={`/agencies/${id}/route-analysis?${new URLSearchParams(s.query)}`}>{s.title}</Link>
        <button aria-label={`${t("remove")}: ${s.title}`} onClick={() => { if (deleteAnalysis(s.id)) setSaved(readAnalyses()); else setNotice(t("saveFailed")); }}>{t("remove")}</button>
      </li>)}</ul>
    </section> : <>
      <TabFilterBar />
      <h2>{agencies.data?.find((a) => a.agency_id === id)?.agency_name}{FILTER_SEPARATOR}{ctx.from} – {ctx.to}</h2>
      <section><div className="focus-header"><h2>{t("trend")}</h2><div className="focus-actions"><button className="btn-ghost" disabled={!days.length || !!trend.error || trend.isFetching} onClick={() => downloadCsv(`trend-${id}-${ctx.from}-${ctx.to}`, [
        ["definition", JSON.stringify(trend.data?.definition)], [], ...buildCsv(days, daysColumns, ctx),
      ])}>{t("csv")}</button></div></div>
      <AsyncSection loading={trend.isPending} error={trend.error} onRetry={() => void trend.refetch()} data={trend.data} hasContent={() => days.length > 0} empty={<EmptyState title={t("empty")} reasons={emptyReasons} recoveries={emptyRecoveries} />}>
        {() => <><p className="focus-muted">{t("mean")}{FILTER_SEPARATOR}{t("coverage", { from: days[0]?.date, to: days.at(-1)?.date })}</p><div ref={chartWrapRef}><PeriodChart days={days} /></div></>}
      </AsyncSection></section>
      <section><div className="focus-header"><h2>{t("routesToCheck")}</h2><div className="focus-actions"><button className="btn-ghost" disabled={!rows.length || !!ranking.error || ranking.isFetching} onClick={() => downloadCsv(`patterns-${id}-${ctx.from}-${ctx.to}`, [
        ["definition", JSON.stringify(ranking.data?.definition)], [], ...buildCsv(rows, rankingColumns, ctx),
      ])}>{t("csv")}</button></div></div>
      <AsyncSection loading={ranking.isPending} error={ranking.error} onRetry={() => void ranking.refetch()} data={ranking.data} hasContent={() => rows.length > 0} empty={<EmptyState title={t("empty")} reasons={emptyReasons} recoveries={emptyRecoveries} />}>
        {() => <div className="focus-table-wrap"><table style={SHARED_TABLE}><thead><tr><th style={th()}>{t("pattern")}</th><th style={th()}>{t("days")}</th><th style={th()}>{t("mean")}</th><th style={th()}>{t("samples")}</th><th style={th()} /></tr></thead><tbody>
          {rows.map((row, i) => <tr key={`${row[0]}-${row[1]}-${i}`}><td style={td()}>{names.format(String(row[0]))}</td><td style={td()}>{String(row[1] ?? "—")}</td><td style={td()}>{row[2] == null ? "—" : Number(row[2]).toFixed(1)}</td><td style={td()}>{String(row[5] ?? "—")}</td><td style={td()}>
            <Link to={`/agencies/${id}/route-analysis?${(() => { const next = new URLSearchParams(queryString); next.set("routes", String(row[0])); if (row[1] === "平日" || row[1] === "土日祝") next.set("service", row[1]); return next.toString(); })()}`}>{t("open")}</Link>{/* i18n-ignore: query contract */}
          </td></tr>)}
        </tbody></table></div>}
      </AsyncSection></section>
      <p className="focus-muted">{t("reportNote")}</p>
      <details><summary>{t("definitions")}</summary><p>{ctx.from} – {ctx.to}{FILTER_SEPARATOR}{ctx.routes.join(", ") || t("allPatterns")}</p>
        {trend.data && <DefinitionMetaBlock definition={trend.data.definition} />}
        <Link to={`/agencies/${id}/analysis/trend?${queryString}`}>{t("advanced")} →</Link>
      </details>
    </>}
  </div>;
}
