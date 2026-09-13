import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRouteShape } from "../api/hooks";
import { useRangeContext, isoDaysBefore } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import { AnalysisFilters } from "../components/analysis/AnalysisFilters";
import { StopChart } from "../components/analysis/StopChart";
import { orderedStops, matchedPrevious } from "../components/analysis/stopSeries";
import { AnalysisMap } from "../components/analysis/AnalysisMap";
import { saveAnalysis } from "../components/analysis/savedAnalyses";
import { downloadCsv } from "../components/analysis/csv";
import { AsyncSection } from "../components/AsyncSection";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import "../styles/focusedAnalysis.css";

export function RouteAnalysisTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { t } = useTranslation("design");
  const [ctx] = useRangeContext();
  const [params, setParams] = useSearchParams();
  const compare = params.get("compare") === "1";
  const route = ctx.routes.length === 1 ? ctx.routes[0] : null;
  const query = useRouteShape(id, route, ctx);
  const prevCtx = { ...ctx, from: isoDaysBefore(ctx.from, 7), to: isoDaysBefore(ctx.to, 7) };
  const previous = useRouteShape(id, compare ? route : null, prevCtx);
  const names = useRouteNames(id);
  const [selection, setSelection] = useState<{ route: string | null; sequence: number } | null>(null);
  const [notice, setNotice] = useState("");
  const stops = query.data ? orderedStops(query.data) : [];
  const prevStops = compare && previous.data && !previous.error ? orderedStops(previous.data) : [];
  const selected = stops.find((s) => selection?.route === route && s.stop_sequence === selection.sequence) ?? stops.find((s) => s.avg_min != null) ?? stops[0];
  return <div className="focus-page">
    <header className="focus-header"><h1>{t("investigate")}</h1><div className="focus-actions">
      <button disabled={!query.data?.stops.length || !!query.error} onClick={() => downloadCsv(`stops-${id}-${route}-${ctx.from}-${ctx.to}`, [
        ["agency_id", "route_code", "from", "to", "dow", "time_band", "service", "stop_sequence", "stop_id", "stop_name", "mean_departure_delay_minutes", "observations", "comparison_mean_minutes"],
        ...stops.map((s) => [id, route, ctx.from, ctx.to, ctx.dow, ctx.time_band, ctx.service, s.stop_sequence, s.stop_id, s.stop_name, s.avg_min, s.samples, matchedPrevious(s, prevStops)]),
      ])}>{t("csv")}</button>
      <button disabled={!id || !query.data?.stops.length || !!query.error} onClick={() => { try { saveAnalysis(id!, `${names.format(route)} · ${ctx.from} – ${ctx.to}`, ctx, compare); setNotice(t("saved")); } catch { setNotice(t("saveFailed")); } }}>{t("save")}</button>
    </div></header>
    {notice && <span role="status">{notice}</span>}
    <AnalysisFilters agencyId={id} />
    {!route ? <EmptyState title={t("choose")} hint={t("filterNote")} /> : <AsyncSection loading={query.isPending} error={query.error} onRetry={() => void query.refetch()} data={query.data} hasContent={(d) => d.stops.length > 0} empty={<EmptyState title={t("empty")} />}>
      {() => <>
        <div className="focus-header"><div><h2>{t("stopDelay")}</h2><span className="focus-muted">{names.format(route)} · {t("mean")}</span></div>
          <label><input type="checkbox" checked={compare} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); if (e.target.checked) next.set("compare", "1"); else next.delete("compare"); return next; })} /> {t("compare")}</label>
        </div>
        {compare && previous.error && <ErrorBanner error={previous.error} onRetry={() => void previous.refetch()} />}
        {compare && previous.isPending && <p className="focus-muted" role="status">{t("previous")} …</p>}
        {compare && !previous.isPending && !previous.error && !prevStops.length && <p>{t("compareUnavailable")}</p>}
        <div className="focus-split">
          <div><StopChart stops={stops} previous={prevStops} selected={selected?.stop_sequence ?? 0} onSelect={(sequence) => setSelection({ route, sequence })} />
            <p className="focus-muted">{t("selected")} {ctx.from} – {ctx.to}{compare && ` · ${t("previous")} ${prevCtx.from} – ${prevCtx.to}`}</p>
          </div>
          <aside className="focus-aside"><label>{t("selectedStop")}<select style={{ width: "100%", margin: "12px 0" }} value={selected?.stop_sequence ?? ""} onChange={(e) => setSelection({ route, sequence: Number(e.target.value) })}>
            {stops.map((s) => <option key={s.stop_sequence} value={s.stop_sequence}>{s.stop_name}</option>)}
          </select></label><p><strong>{selected?.avg_min == null ? "—" : selected.avg_min.toFixed(1)}</strong> {t("minutes")}</p><p>{t("samples")} {selected?.samples ?? 0}</p></aside>
        </div>
        <p className="focus-muted">{t("caveat")}</p>
        <AnalysisMap data={query.data!} selected={selected} />
        <details><summary>{t("details")}</summary><div className="focus-table-wrap"><table className="focus-table"><thead><tr><th>{t("stop")}</th><th>{t("mean")}</th><th>{t("samples")}</th></tr></thead><tbody>
          {stops.map((s) => <tr key={s.stop_sequence}><td>{s.stop_name}</td><td>{s.avg_min ?? t("missing")}</td><td>{s.samples}</td></tr>)}
        </tbody></table></div></details>
      </>}
    </AsyncSection>}
  </div>;
}
