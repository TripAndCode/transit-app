import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgencies, useReport } from "../api/hooks";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { RankingRow, TrendDay } from "../api/types";
import { TabFilterBar } from "../components/TabFilterBar";
import { downloadCsv } from "../components/analysis/csv";
import { deleteAnalysis, readAnalyses } from "../components/analysis/savedAnalyses";
import { PeriodChart } from "../components/analysis/PeriodChart";
import { AsyncSection } from "../components/AsyncSection";
import { EmptyState } from "../components/EmptyState";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import "../styles/focusedAnalysis.css";

const DAYS_CSV_HEADER = ["date", "mean_departure_delay_minutes", "observations"];
function daysToCsvRows(days: TrendDay[]) {
  return [DAYS_CSV_HEADER, ...days.map((d) => [d.date, d.avg_min, d.samples])];
}
const RANKING_CSV_HEADER = ["route_code", "service_type", "mean_minutes", "median_minutes", "p90_minutes", "observations"];
function rankingToCsvRows(rows: RankingRow[]) {
  return [RANKING_CSV_HEADER, ...rows];
}

export function ReportsHomeTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { t } = useTranslation("design");
  const [ctx] = useRangeContext();
  const [params, setParams] = useSearchParams();
  const savedTab = params.get("view") === "saved";
  const trend = useReport(id, savedTab ? null : "trend", ctx);
  const ranking = useReport(id, savedTab ? null : "ranking", ctx);
  const agencies = useAgencies();
  const names = useRouteNames(id);
  const [saved, setSaved] = useState(readAnalyses);
  const [notice, setNotice] = useState("");
  const [shareNotice, setShareNotice] = useState("");
  // Both queries pin their own report_type above, so these narrowings can
  // only fall through while the response for that type is still in flight.
  const trendPayload = trend.data?.report_type === "trend" ? trend.data.rows[0] : undefined;
  const days = (trendPayload?.days ?? []).filter((d) => Number.isFinite(d.avg_min) && d.samples > 0).sort((a, b) => a.date.localeCompare(b.date));
  const rows: RankingRow[] = ranking.data?.report_type === "ranking" ? ranking.data.rows : [];
  const queryString = ctxToQueryString(ctx);
  const metadata = [["agency_id", "from", "to", "dow", "time_band", "service", "route_codes"], [id, ctx.from, ctx.to, ctx.dow, ctx.time_band, ctx.service, ctx.routes.join(",")]];
  async function copyShareLink() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}?${queryString}`);
      setShareNotice(t("copied"));
    } catch {
      setShareNotice(t("copyFailed"));
    }
  }
  return <div className="focus-page">
    <header className="focus-header"><div><h1>{t("reports")}</h1><p>{t("reportTitle")}</p></div>
      {!savedTab && <div className="focus-actions"><button onClick={() => window.print()}>{t("print")}</button></div>}
    </header>
    <div className="focus-tabs">{[false, true].map((isSaved) => <button key={String(isSaved)} aria-pressed={savedTab === isSaved} onClick={() => {
      setSaved(readAnalyses());
      setParams((old) => { const next = new URLSearchParams(old); if (isSaved) next.set("view", "saved"); else next.delete("view"); return next; });
    }}>{t(isSaved ? "savedAnalyses" : "summary")}</button>)}</div>
    {notice && <p role="status">{notice}</p>}
    {savedTab ? <section><p className="focus-muted">{t("localOnly")}</p>
      {!saved.some((s) => s.agencyId === id) && <EmptyState title={t("noSaved")} />}
      <ul className="focus-saved">{saved.filter((s) => s.agencyId === id).map((s) => <li key={s.id}>
        <Link to={`/agencies/${id}/route-analysis?${new URLSearchParams(s.query)}`}>{s.title}</Link>
        <button aria-label={`${t("remove")}: ${s.title}`} onClick={() => { try { deleteAnalysis(s.id); setSaved(readAnalyses()); } catch { setNotice(t("saveFailed")); } }}>{t("remove")}</button>
      </li>)}</ul>
    </section> : <>
      <TabFilterBar />
      <h2>{agencies.data?.find((a) => a.agency_id === id)?.agency_name} · {ctx.from} – {ctx.to}</h2>
      <section><div className="focus-header"><h2>{t("trend")}</h2><div className="focus-actions"><button className="btn-ghost" disabled={!days.length || !!trend.error || trend.isFetching} onClick={() => downloadCsv(`trend-${id}-${ctx.from}-${ctx.to}`, [
        ...metadata, [], ["definition", JSON.stringify(trend.data?.definition)], [], ...daysToCsvRows(days),
      ])}>{t("csv")}</button></div></div>
      <AsyncSection loading={trend.isPending} error={trend.error} onRetry={() => void trend.refetch()} data={trend.data} hasContent={() => days.length > 0} empty={<EmptyState title={t("empty")} />}>
        {() => <><p className="focus-muted">{t("mean")} · {t("coverage", { from: days[0]?.date, to: days.at(-1)?.date })}</p><PeriodChart days={days} /></>}
      </AsyncSection></section>
      <section><div className="focus-header"><h2>{t("routesToCheck")}</h2><div className="focus-actions"><button className="btn-ghost" disabled={!rows.length || !!ranking.error || ranking.isFetching} onClick={() => downloadCsv(`patterns-${id}-${ctx.from}-${ctx.to}`, [
        ...metadata, [], ["definition", JSON.stringify(ranking.data?.definition)], [], ...rankingToCsvRows(rows),
      ])}>{t("csv")}</button></div></div>
      <AsyncSection loading={ranking.isPending} error={ranking.error} onRetry={() => void ranking.refetch()} data={ranking.data} hasContent={() => rows.length > 0} empty={<EmptyState title={t("empty")} />}>
        {() => <div className="focus-table-wrap"><table className="focus-table"><thead><tr><th>{t("pattern")}</th><th>{t("days")}</th><th>{t("mean")}</th><th>{t("samples")}</th><th /></tr></thead><tbody>
          {rows.map((row, i) => <tr key={`${row[0]}-${row[1]}-${i}`}><td>{names.format(String(row[0]))}</td><td>{String(row[1] ?? "—")}</td><td>{row[2] == null ? "—" : Number(row[2]).toFixed(1)}</td><td>{String(row[5] ?? "—")}</td><td>
            <Link to={`/agencies/${id}/route-analysis?${(() => { const next = new URLSearchParams(queryString); next.set("routes", String(row[0])); if (row[1] === "平日" || row[1] === "土日祝") next.set("service", row[1]); return next.toString(); })()}`}>{t("open")}</Link>{/* i18n-ignore: query contract */}
          </td></tr>)}
        </tbody></table></div>}
      </AsyncSection></section>
      <p className="focus-muted">{t("reportNote")}</p>
      <details><summary>{t("definitions")}</summary><p>{ctx.from} – {ctx.to} · {ctx.routes.join(", ") || t("allPatterns")}</p>
        {trend.data && <DefinitionMetaBlock definition={trend.data.definition} />}
        <Link to={`/agencies/${id}/analysis/trend?${queryString}`}>{t("advanced")} →</Link>
      </details>
      <footer className="focus-report-footer">
        {shareNotice && <span role="status" className="focus-muted">{shareNotice}</span>}
        <div className="focus-actions">
          <button className="btn-ghost" disabled={!rows.length && !days.length} onClick={() => downloadCsv(`report-${id}-${ctx.from}-${ctx.to}`, [
            ...metadata, [], ...daysToCsvRows(days), [], ...rankingToCsvRows(rows),
          ])}>{t("csv")}</button>
          <button className="btn-ghost" onClick={() => void copyShareLink()}>{t("shareLink")}</button>
        </div>
      </footer>
    </>}
  </div>;
}
