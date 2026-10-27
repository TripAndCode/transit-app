import { Suspense, lazy, use, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RouteTitleTransition } from "../components/RouteTitleTransition";
import { RouteLabel } from "../components/RouteLabel";
import { useTranslation } from "react-i18next";
import { useRouteShape, useRouteTrips } from "../api/hooks";
import { useJumpToLatestDataRange } from "../api/latestDataWindow";
import { ScopeRouteContext, useScope, isoDaysBefore } from "../api/scope";
import { useUrlPatch, useUrlState } from "../api/useUrlState";
import { useRouteNames } from "../api/useRouteNames";
import { useAgencyId } from "../api/useAgencyId";
import { PinRouteButton } from "../components/PinRouteButton";
import { useScreenQuery, withQuery } from "../api/screenScope";
import type { RouteShapeStop } from "../api/types";
import { AnalysisFilters } from "../components/analysis/AnalysisFilters";
import { DelayBandLegend, StopChart } from "../components/analysis/StopChart";
import { orderedStops, matchedPrevious } from "../components/analysis/stopSeries";
import { RouteSummaryStrip } from "../components/analysis/RouteSummaryStrip";
import { RouteVariantPicker } from "../components/analysis/RouteVariantPicker";
import { initialStop, largestRises, previousWindow, windowDays } from "../components/analysis/routeDossier";
import { MareyDiagram } from "../components/charts/MareyDiagram";
import { scrubMapPositions, type ScrubMapPosition } from "../components/charts/mareyScrub";
import { timeWindowForBand } from "../components/charts/mareyLayout";
import { SkeletonChart } from "../components/Skeleton";
import { saveAnalysis } from "../components/analysis/savedAnalyses";
import { scopeTitle } from "../components/scope/scopePhrases";
import { buildCsv, downloadCsv, type CsvColumn } from "../components/analysis/csv";
import { AsyncSection } from "../components/AsyncSection";
import { EmptyState } from "../components/EmptyState";
import { SHARED_TABLE, td, th } from "../components/tableStyles";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { ErrorBanner } from "../components/ErrorBanner";
import "../styles/focusedAnalysis.css";
import { fixedTenth, formatDateRange, formatNumber } from "../utils/format";

// Dynamic, not a static import: MapLibre would otherwise ride into this tab's
// chunk and download with every route's page. Loaded this way, it arrives
// only once the Map sub-tab is first opened.
const AnalysisMap = lazy(() =>
  import("../components/analysis/AnalysisMap").then((m) => ({ default: m.AnalysisMap })),
);

/** Sub-tabs in the order they are rendered — also the order the arrow keys
 *  walk, and the closed set `sub_tab` may hold. */
const SUB_TABS = ["trend", "marey", "map", "byStop"] as const;
const NO_MAP_POSITIONS: ScrubMapPosition[] = [];
type SubTab = (typeof SUB_TABS)[number];

const SUB_TAB_LABEL_KEYS: Record<SubTab, string> = {
  trend: "tabTrend",
  marey: "tabMarey",
  map: "tabMap",
  byStop: "tabByStop",
};

const tabId = (sub: SubTab) => `route-analysis-tab-${sub}`;
const panelId = (sub: SubTab) => `route-analysis-panel-${sub}`;

export function RouteAnalysisTab() {
  const id = useAgencyId();
  const { t } = useTranslation("design");
  const screenQuery = useScreenQuery();
  const [ctx, update] = useScope();
  // On a route's own page the route is the page, not a filter to clear.
  const pageRoute = use(ScopeRouteContext);
  const jumpToLatestData = useJumpToLatestDataRange(id);
  const [params, setParams] = useSearchParams();
  const compare = params.get("compare") === "1";
  const route = ctx.routes.length === 1 ? ctx.routes[0] : null;
  const [variantParam] = useUrlState<string>("variant", "");
  const query = useRouteShape(id, route, ctx, variantParam || null);
  // The equal-length window just before, which shares no day with this one,
  // on the variant this window shows so the two lines are the same stops.
  const prevCtx = { ...ctx, ...previousWindow(ctx.from, ctx.to) };
  const previous = useRouteShape(id, compare && query.data ? route : null, prevCtx, query.data?.shape_id ?? null);
  const names = useRouteNames(id);
  // Two plain string keys rather than one JSON-shaped one, so a shared link
  // shows a human-readable stop selection. `stopRouteParam` guards against a
  // stale `stop_seq` matching a different route's stop after the route
  // changes -- both are cleared together in `setSelection`.
  const [stopRouteParam] = useUrlState<string>("stop_route", "");
  const [stopSeqParam] = useUrlState<string>("stop_seq", "");
  const patchUrl = useUrlPatch();
  const selection = stopRouteParam && stopSeqParam ? { route: stopRouteParam, sequence: Number(stopSeqParam) } : null;
  function setSelection(next: { route: string | null; sequence: number }) {
    patchUrl({ stop_route: next.route, stop_seq: String(next.sequence) });
  }
  // Another variant numbers its stops afresh, so the stop picked on this one
  // goes with it.
  function pickVariant(shapeId: string) {
    patchUrl({ variant: shapeId, stop_route: null, stop_seq: null });
  }
  const [notice, setNotice] = useState("");
  const [scrubSec, setScrubSec] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useUrlState<SubTab>("sub_tab", "trend", SUB_TABS);
  // Seeded from the URL so `?sub_tab=map` restores the map, and sticky
  // afterwards so leaving the sub-tab doesn't tear down the WebGL context.
  const [mapVisited, setMapVisited] = useState(activeTab === "map");
  const tabRefs = useRef<Partial<Record<SubTab, HTMLButtonElement | null>>>({});
  function selectTab(next: SubTab) {
    setActiveTab(next);
    if (next === "map") setMapVisited(true);
  }
  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, sub: SubTab) {
    const index = SUB_TABS.indexOf(sub);
    const next =
      event.key === "ArrowRight" ? SUB_TABS[(index + 1) % SUB_TABS.length]
      : event.key === "ArrowLeft" ? SUB_TABS[(index - 1 + SUB_TABS.length) % SUB_TABS.length]
      : event.key === "Home" ? SUB_TABS[0]
      : event.key === "End" ? SUB_TABS[SUB_TABS.length - 1]
      : null;
    if (!next) return;
    event.preventDefault();
    selectTab(next);
    tabRefs.current[next]?.focus();
  }
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
  // Nothing on this page is answerable without an agency, and every query
  // above is already disabled for a null id.
  if (id == null) return null;
  // A scrub outside the chosen band's window means nothing to the diagram,
  // so it is ignored (derived, not reset) until a band whose window holds it
  // is chosen again.
  const bandWindow = timeWindowForBand(ctx.time_band);
  const effectiveScrub = scrubSec != null && scrubSec >= bandWindow.startSec && scrubSec <= bandWindow.endSec ? scrubSec : null;
  // Only the map tab shows positions, and the scrub cannot move while it is on
  // screen, so they are derived there alone rather than on every scrub step.
  const mapPositions =
    activeTab === "map" && effectiveScrub != null && trips.data && query.data
      ? scrubMapPositions(trips.data.trips, query.data, bandWindow, effectiveScrub)
      : NO_MAP_POSITIONS;
  const stops = query.data ? orderedStops(query.data) : [];
  const axis = stops.map((s) => ({ stop_sequence: s.stop_sequence, stop_name: s.stop_name }));
  const prevStops = compare && previous.data && !previous.error ? orderedStops(previous.data) : [];
  const selected = stops.find((s) => selection?.route === route && s.stop_sequence === selection.sequence) ?? initialStop(stops);
  const [rise, nextRise] = largestRises(query.data?.segments, stops);
  const lastSequence = stops.at(-1)?.stop_sequence;
  const missingText = (s: { stop_sequence: number }) => t(s.stop_sequence === lastSequence ? "terminus" : "missing");
  const stopColumns: CsvColumn<RouteShapeStop>[] = [
    { header: "route_code", value: () => route },
    { header: "shape_id", value: () => query.data?.shape_id ?? null },
    { header: "stop_sequence", value: (s) => s.stop_sequence },
    { header: "stop_id", value: (s) => s.stop_id },
    { header: "stop_name", value: (s) => s.stop_name },
    { header: "mean_departure_delay_minutes", value: (s) => s.avg_min },
    { header: "observations", value: (s) => s.samples },
    { header: "comparison_mean_minutes", value: (s) => matchedPrevious(s, prevStops) },
  ];
  return <div className="focus-page">
    {route && <nav className="focus-breadcrumb" aria-label={t("breadcrumb")}>
      <Link to={withQuery(`/agencies/${id}/routes`, screenQuery(String(id), "routes"))}>{t("routesCrumb")}</Link>
      <span aria-hidden="true"> / </span>
      <span aria-current="page">{names.format(route)}</span>
    </nav>}
    <header className="focus-header"><div>
      {/* A route's page is named by its route; the question it answers sits
          beneath. Without a route the question is all there is to say. */}
      {route ? (
        <RouteTitleTransition>
          <h1><RouteLabel code={route} names={names} /></h1>
        </RouteTitleTransition>
      ) : (
        <h1>{t("investigate")}</h1>
      )}
      {route && <p className="focus-muted focus-subtitle">{t("investigate")}</p>}
    </div><div className="focus-actions">
      {route && <PinRouteButton agencyId={id} code={route} />}
      <button className="btn-ghost" disabled={!query.data?.stops.length || !!query.error || (compare && (previous.isFetching || !!previous.error))} onClick={() => downloadCsv(`stops-${id}-${route}-${ctx.from}-${ctx.to}`, [
        ...buildCsv(stops, stopColumns, ctx),
        [], ["comparison_from", "comparison_to"], [compare ? prevCtx.from : "", compare ? prevCtx.to : ""],
      ])}>{t("csv")}</button>
      <button disabled={!query.data?.stops.length || !!query.error} onClick={() => setNotice(t(saveAnalysis(id, scopeTitle(ctx, { t, agencyName: "", routeLabel: names.format }), ctx, compare) ? "saved" : "saveFailed"))}>{t("save")}</button>
    </div></header>
    {notice && <span role="status">{notice}</span>}
    {route && <RouteSummaryStrip agencyId={id} route={route} ctx={ctx} />}
    <AnalysisFilters agencyId={id} />
    {!route ? <EmptyState title={t("choose")} hint={t("filterNote")} /> : <AsyncSection loading={query.isPending} error={query.error} onRetry={() => void query.refetch()} data={query.data} hasContent={(d) => d.stops.length > 0} empty={<EmptyState title={t("empty")}
      reasons={buildFilterCtxReasons(ctx, t, names.format)}
      recoveries={buildFilterCtxRecoveries({
        ctx: pageRoute == null ? ctx : { ...ctx, routes: [] },
        onClearRoutes: () => update({ routes: null }),
        onResetService: () => update({ service: "all" }),
        jumpToLatestData,
        t,
      })}
    />}>
      {() => <>
        <div className="focus-header"><div><h2>{t("stopDelay")}</h2><span className="focus-muted">{t("mean")}</span></div>
          <label><input type="checkbox" checked={compare} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); if (e.target.checked) next.set("compare", "1"); else next.delete("compare"); return next; })} /> {t("compare")}</label>
        </div>
        <RouteVariantPicker variants={query.data?.variants ?? []} shown={query.data?.shape_id ?? null} onPick={pickVariant} />
        {rise && <div className="route-rise">
          <p className="route-rise__headline">{t("riseHeadline", { from: rise.from.stop_name, to: rise.to.stop_name, min: fixedTenth(rise.increaseMin) })}</p>
          {nextRise && <p className="focus-muted">{t("riseNext", { from: nextRise.from.stop_name, to: nextRise.to.stop_name, min: fixedTenth(nextRise.increaseMin) })}</p>}
        </div>}
        {compare && previous.error && <ErrorBanner error={previous.error} onRetry={() => void previous.refetch()} />}
        {compare && previous.isPending && <p className="focus-muted" role="status">{t("previous", { days: windowDays(ctx.from, ctx.to) })} …</p>}
        {compare && !previous.isPending && !previous.error && !prevStops.length && <p>{t("compareUnavailable")}</p>}
        <div className="focus-tabs" role="tablist">
          {/* Roving tabindex: only the selected tab is in the tab order, and
              the arrow keys move between them -- so Tab leaves the tablist
              for the panel rather than stepping through every sub-tab. */}
          {SUB_TABS.map((sub) => <button
            key={sub}
            type="button"
            role="tab"
            id={tabId(sub)}
            aria-controls={panelId(sub)}
            aria-selected={activeTab === sub}
            tabIndex={activeTab === sub ? 0 : -1}
            ref={(el) => { tabRefs.current[sub] = el; }}
            onKeyDown={(e) => onTabKeyDown(e, sub)}
            onClick={() => selectTab(sub)}
          >{t(SUB_TAB_LABEL_KEYS[sub])}</button>)}
        </div>
        <div className="focus-split">
          <div>
            {activeTab === "trend" && <div className="focus-tab-panel" role="tabpanel" id={panelId("trend")} aria-labelledby={tabId("trend")}>
              <div className="focus-actions focus-muted"><span style={{ color: "var(--accent)" }}>― {t("selected")}</span>{compare && <span>┄ {t("previous", { days: windowDays(ctx.from, ctx.to) })}</span>}{rise && <span><span aria-hidden="true" className="route-rise__swatch" /> {t("riseHighlight")}</span>}<span>○ {t("missing")}</span><DelayBandLegend /></div>
              <p className="focus-muted focus-chart-hint">{t("chartScrollHint", { count: stops.length })}</p>
              <div className="focus-chart-scroll"><StopChart stops={stops} previous={prevStops} selected={selected?.stop_sequence ?? 0} onSelect={(sequence) => setSelection({ route, sequence })} highlight={rise ? { from: rise.from.stop_sequence, to: rise.to.stop_sequence } : null} /></div>
              <p className="focus-muted">{t("selected")} {formatDateRange(ctx.from, ctx.to)}{compare && ` · ${t("previous", { days: windowDays(ctx.from, ctx.to) })} ${formatDateRange(prevCtx.from, prevCtx.to)}`}</p>
            </div>}
            {activeTab === "marey" && <div className="focus-tab-panel" role="tabpanel" id={panelId("marey")} aria-labelledby={tabId("marey")}>
              <AsyncSection loading={trips.isPending} error={trips.error} onRetry={() => void trips.refetch()} data={trips.data} hasContent={(d) => d.trips.length > 0} empty={<EmptyState title={t("empty")} />} skeleton={<SkeletonChart height={320} />}>
                {(d) => <MareyDiagram trips={d.trips} previousTrips={previousTrips.data?.trips ?? []} axis={axis} band={ctx.time_band} truncated={d.truncated} date={d.date} scrubSec={effectiveScrub} onScrub={setScrubSec} />}
              </AsyncSection>
            </div>}
            {mapVisited && <div className={`focus-tab-panel${activeTab === "map" ? "" : " focus-tab-panel--hidden"}`} role="tabpanel" id={panelId("map")} aria-labelledby={tabId("map")}>
              <Suspense fallback={<SkeletonChart height={420} />}>
                <AnalysisMap data={query.data!} selected={selected} height={420} visible={activeTab === "map"} positions={mapPositions} />
              </Suspense>
            </div>}
            {activeTab === "byStop" && <div className="focus-tab-panel" role="tabpanel" id={panelId("byStop")} aria-labelledby={tabId("byStop")}>
              <div className="focus-table-wrap"><table style={SHARED_TABLE}><thead><tr><th style={th()}>{t("stop")}</th><th style={th()}>{t("mean")}</th><th style={th()}>{t("samples")}</th></tr></thead><tbody>
                {stops.map((s) => <tr key={s.stop_sequence}><td style={td()}>{s.stop_name}</td><td style={td()}>{s.avg_min == null ? missingText(s) : fixedTenth(s.avg_min)}</td><td style={td()}>{formatNumber(s.samples)}</td></tr>)}
              </tbody></table></div>
            </div>}
            <p className="focus-muted">{t("caveat")}</p>
            {/* A list names a route without its route_id; its own page keeps
                it, for anyone matching the page against the feed. */}
            <p className="focus-muted focus-route-id">{t("routeId", { id: names.parts.get(route)?.id ?? route })}</p>
          </div>
          <aside className="focus-aside"><label>{t("selectedStop")}<select style={{ width: "100%", margin: "12px 0" }} value={selected?.stop_sequence ?? ""} onChange={(e) => setSelection({ route, sequence: Number(e.target.value) })}>
            {stops.map((s) => <option key={s.stop_sequence} value={s.stop_sequence}>{s.stop_name}</option>)}
          </select></label><p><strong className="num">{selected?.avg_min == null ? "—" : fixedTenth(selected.avg_min)}</strong> {t("minutes")}</p><p>{t("samples")} {formatNumber(selected?.samples ?? 0)}</p></aside>
        </div>
      </>}
    </AsyncSection>}
  </div>;
}
