import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRouteShape, useRouteTrips } from "../api/hooks";
import { useJumpToLatestDataRange } from "../api/defaultRangeAnchor";
import { useRangeContext, isoDaysBefore } from "../api/rangeContext";
import { useUrlPatch, useUrlState } from "../api/useUrlState";
import { useRouteNames } from "../api/useRouteNames";
import { useAgencyId } from "../api/useAgencyId";
import type { RouteShapeStop } from "../api/types";
import { AnalysisFilters } from "../components/analysis/AnalysisFilters";
import { StopChart } from "../components/analysis/StopChart";
import { orderedStops, matchedPrevious } from "../components/analysis/stopSeries";
import { AnalysisMap } from "../components/analysis/AnalysisMap";
import { MareyDiagram } from "../components/charts/MareyDiagram";
import { SkeletonChart } from "../components/Skeleton";
import { saveAnalysis } from "../components/analysis/savedAnalyses";
import { buildCsv, downloadCsv, type CsvColumn } from "../components/analysis/csv";
import { AsyncSection } from "../components/AsyncSection";
import { EmptyState } from "../components/EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { ErrorBanner } from "../components/ErrorBanner";
import "../styles/focusedAnalysis.css";

export function RouteAnalysisTab() {
  const id = useAgencyId();
  const { t } = useTranslation("design");
  const [ctx, update] = useRangeContext();
  const jumpToLatestData = useJumpToLatestDataRange(id);
  const [params, setParams] = useSearchParams();
  const compare = params.get("compare") === "1";
  const route = ctx.routes.length === 1 ? ctx.routes[0] : null;
  const query = useRouteShape(id, route, ctx);
  const prevCtx = { ...ctx, from: isoDaysBefore(ctx.from, 7), to: isoDaysBefore(ctx.to, 7) };
  const previous = useRouteShape(id, compare ? route : null, prevCtx);
  const names = useRouteNames(id);
  // Two plain string keys rather than one JSON-shaped one, so a shared link
  // shows a human-readable stop selection. `stopRouteParam` guards against a
  // stale `stop_seq` matching a different route's stop after the route
  // changes (the same invalidation the old `selection.route === route` check
  // did) -- both are cleared together in `setSelection`.
  const [stopRouteParam] = useUrlState<string>("stop_route", "");
  const [stopSeqParam] = useUrlState<string>("stop_seq", "");
  const patchUrl = useUrlPatch();
  const selection = stopRouteParam && stopSeqParam ? { route: stopRouteParam, sequence: Number(stopSeqParam) } : null;
  function setSelection(next: { route: string | null; sequence: number }) {
    patchUrl({ stop_route: next.route, stop_seq: String(next.sequence) });
  }
  const [notice, setNotice] = useState("");
  const [activeTab, setActiveTab] = useUrlState<"map" | "trend" | "marey" | "byStop">("sub_tab", "trend");
  const [mapVisited, setMapVisited] = useState(false);
  // No `date`: the server answers for the route's own latest observed day.
  // The range filter's end date is routinely a day this route did not run, and
  // a diagram of nothing teaches nothing.
  const trips = useRouteTrips(id, route, { timeBand: ctx.time_band });
  // The ghost week can only be asked for once the current day is known, since
  // it is that day minus seven, not the filter's end date minus seven.
  const ghostDate = compare && trips.data?.date ? isoDaysBefore(trips.data.date, 7) : null;
  const previousTrips = useRouteTrips(id, ghostDate ? route : null, {
    date: ghostDate,
    timeBand: ctx.time_band,
  });
  const stops = query.data ? orderedStops(query.data) : [];
  const prevStops = compare && previous.data && !previous.error ? orderedStops(previous.data) : [];
  const selected = stops.find((s) => selection?.route === route && s.stop_sequence === selection.sequence) ?? stops.find((s) => s.avg_min != null) ?? stops[0];
  const stopColumns: CsvColumn<RouteShapeStop>[] = [
    { header: "route_code", value: () => route },
    { header: "stop_sequence", value: (s) => s.stop_sequence },
    { header: "stop_id", value: (s) => s.stop_id },
    { header: "stop_name", value: (s) => s.stop_name },
    { header: "mean_departure_delay_minutes", value: (s) => s.avg_min },
    { header: "observations", value: (s) => s.samples },
    { header: "comparison_mean_minutes", value: (s) => matchedPrevious(s, prevStops) },
  ];
  return <div className="focus-page">
    <header className="focus-header"><h1>{t("investigate")}</h1><div className="focus-actions">
      <button className="btn-ghost" disabled={!query.data?.stops.length || !!query.error || (compare && (previous.isFetching || !!previous.error))} onClick={() => downloadCsv(`stops-${id}-${route}-${ctx.from}-${ctx.to}`, [
        ...buildCsv(stops, stopColumns, ctx),
        [], ["comparison_from", "comparison_to"], [compare ? prevCtx.from : "", compare ? prevCtx.to : ""],
      ])}>{t("csv")}</button>
      <button disabled={!id || !query.data?.stops.length || !!query.error} onClick={() => setNotice(t(saveAnalysis(id!, `${names.format(route)} · ${ctx.from} – ${ctx.to}`, ctx, compare) ? "saved" : "saveFailed"))}>{t("save")}</button>
    </div></header>
    {notice && <span role="status">{notice}</span>}
    <AnalysisFilters agencyId={id} />
    {!route ? <EmptyState title={t("choose")} hint={t("filterNote")} /> : <AsyncSection loading={query.isPending} error={query.error} onRetry={() => void query.refetch()} data={query.data} hasContent={(d) => d.stops.length > 0} empty={<EmptyState title={t("empty")}
      reasons={buildFilterCtxReasons(ctx, t)}
      recoveries={buildFilterCtxRecoveries({
        ctx,
        onClearRoutes: () => update({ routes: null }),
        onResetService: () => update({ service: "all" }),
        jumpToLatestData,
        t,
      })}
    />}>
      {() => <>
        <div className="focus-header"><div><h2>{t("stopDelay")}</h2><span className="focus-muted">{names.format(route)} · {t("mean")}</span></div>
          <label><input type="checkbox" checked={compare} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); if (e.target.checked) next.set("compare", "1"); else next.delete("compare"); return next; })} /> {t("compare")}</label>
        </div>
        {compare && previous.error && <ErrorBanner error={previous.error} onRetry={() => void previous.refetch()} />}
        {compare && previous.isPending && <p className="focus-muted" role="status">{t("previous")} …</p>}
        {compare && !previous.isPending && !previous.error && !prevStops.length && <p>{t("compareUnavailable")}</p>}
        <div className="focus-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={activeTab === "trend"} onClick={() => setActiveTab("trend")}>{t("tabTrend")}</button>
          <button type="button" role="tab" aria-selected={activeTab === "marey"} onClick={() => setActiveTab("marey")}>{t("tabMarey")}</button>
          <button type="button" role="tab" aria-selected={activeTab === "map"} onClick={() => { setActiveTab("map"); setMapVisited(true); }}>{t("tabMap")}</button>
          <button type="button" role="tab" aria-selected={activeTab === "byStop"} onClick={() => setActiveTab("byStop")}>{t("tabByStop")}</button>
        </div>
        <div className="focus-split">
          <div>
            {activeTab === "trend" && <div className="focus-tab-panel">
              <div className="focus-actions focus-muted"><span style={{ color: "var(--accent)" }}>● {t("selected")}</span>{compare && <span>┄ {t("previous")}</span>}<span>○ {t("missing")}</span></div>
              <StopChart stops={stops} previous={prevStops} selected={selected?.stop_sequence ?? 0} onSelect={(sequence) => setSelection({ route, sequence })} />
              <p className="focus-muted">{t("selected")} {ctx.from} – {ctx.to}{compare && ` · ${t("previous")} ${prevCtx.from} – ${prevCtx.to}`}</p>
            </div>}
            {activeTab === "marey" && <div className="focus-tab-panel">
              <AsyncSection loading={trips.isPending} error={trips.error} onRetry={() => void trips.refetch()} data={trips.data} hasContent={(d) => d.trips.length > 0} empty={<EmptyState title={t("empty")} />} skeleton={<SkeletonChart height={320} />}>
                {(d) => <MareyDiagram trips={d.trips} previousTrips={previousTrips.data?.trips ?? []} axis={stops.map((s) => ({ stop_sequence: s.stop_sequence, stop_name: s.stop_name }))} band={ctx.time_band} truncated={d.truncated} date={d.date} />}
              </AsyncSection>
            </div>}
            {mapVisited && <div className={`focus-tab-panel${activeTab === "map" ? "" : " focus-tab-panel--hidden"}`}>
              <AnalysisMap data={query.data!} selected={selected} height={420} visible={activeTab === "map"} />
            </div>}
            {activeTab === "byStop" && <div className="focus-tab-panel">
              <div className="focus-table-wrap"><table className="focus-table"><thead><tr><th>{t("stop")}</th><th>{t("mean")}</th><th>{t("samples")}</th></tr></thead><tbody>
                {stops.map((s) => <tr key={s.stop_sequence}><td>{s.stop_name}</td><td>{s.avg_min ?? t("missing")}</td><td>{s.samples}</td></tr>)}
              </tbody></table></div>
            </div>}
            <p className="focus-muted">{t("caveat")}</p>
          </div>
          <aside className="focus-aside"><label>{t("selectedStop")}<select style={{ width: "100%", margin: "12px 0" }} value={selected?.stop_sequence ?? ""} onChange={(e) => setSelection({ route, sequence: Number(e.target.value) })}>
            {stops.map((s) => <option key={s.stop_sequence} value={s.stop_sequence}>{s.stop_name}</option>)}
          </select></label><p><strong>{selected?.avg_min == null ? "—" : selected.avg_min.toFixed(1)}</strong> {t("minutes")}</p><p>{t("samples")} {selected?.samples ?? 0}</p></aside>
        </div>
      </>}
    </AsyncSection>}
  </div>;
}
