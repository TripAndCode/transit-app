import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { OverviewConcentration } from "../api/types";
import type { RoutePagePayload } from "../api/snapshots";
import type { TierState } from "../api/useCapabilityTier";
import type { RouteNames } from "../api/useRouteNames";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { EmptyState } from "../components/EmptyState";
import { PerformanceStandardView } from "../components/PerformanceStandardPanel";
import { RouteSummaryFigures } from "../components/analysis/RouteSummaryStrip";
import { RouteVariantPicker } from "../components/analysis/RouteVariantPicker";
import { RiseHeadline, SelectedStopAside, StopDelayTable, StopDelayTrend } from "../components/analysis/RouteStopDelay";
import { initialStop, largestRises, pickableVariants } from "../components/analysis/routeDossier";
import { orderedStops } from "../components/analysis/stopSeries";

const NO_STOPS: never[] = [];

/** A route's page as a snapshot stored it, drawn by the live page's own
 *  parts from one language's stored responses: the route's standing, its
 *  performance targets and its delay by stop. The map, the day's trips and
 *  the previous-period comparison read live data and are not stored, so the
 *  page says so rather than drawing them. */
export function RoutePageSnapshot({
  page,
  concentration,
  route,
  from,
  to,
  timeBand,
  names,
  tier,
  tiers,
}: {
  page: RoutePagePayload;
  concentration: OverviewConcentration | undefined;
  route: string;
  from: string;
  to: string;
  timeBand: string;
  names: RouteNames;
  tier: TierState;
  tiers: ReadonlyMap<number, TierState>;
}) {
  const { t } = useTranslation();
  const { t: td } = useTranslation("design");
  const [picked, setPicked] = useState<number | null>(null);
  const shape = page.shape;
  const stops = orderedStops(shape);
  const selected = stops.find((s) => s.stop_sequence === picked) ?? initialStop(stops);
  const [rise, nextRise] = largestRises(shape.segments, stops);
  return (
    <section>
      <DefinitionMetaBlock definition={page.on_time.definition} tiers={tiers} />
      <RouteSummaryFigures
        route={route}
        timeBand={timeBand}
        sources={{ ranking: page.ranking, onTime: page.on_time, worst: page.worst_5min, concentration }}
      />
      <PerformanceStandardView data={page.standards} routeNames={names} tier={tier} />
      <section className="snapshot-route__stops" aria-labelledby="snapshot-stop-delay">
        <div className="focus-header">
          <div>
            <h2 id="snapshot-stop-delay">{td("stopDelay")}</h2>
            <span className="focus-muted">{td("mean")}</span>
          </div>
        </div>
        {stops.length === 0 ? (
          <EmptyState title={td("empty")} />
        ) : (
          <>
            <RouteVariantPicker variants={pickableVariants(shape.variants ?? [], shape.shape_id ?? null, stops)} shown={shape.shape_id ?? null} />
            <RiseHeadline rise={rise} nextRise={nextRise} />
            <div className="focus-split">
              <div>
                <h3>{td("tabTrend")}</h3>
                <StopDelayTrend
                  stops={stops}
                  previous={NO_STOPS}
                  compare={false}
                  selected={selected?.stop_sequence ?? 0}
                  onSelect={setPicked}
                  rise={rise}
                  from={from}
                  to={to}
                />
                <h3>{td("tabByStop")}</h3>
                <StopDelayTable stops={stops} />
                <p className="focus-muted">{td("caveat")}</p>
                <p className="focus-muted focus-route-id">{td("routeId", { id: names.parts.get(route)?.id ?? route })}</p>
              </div>
              <SelectedStopAside stops={stops} selected={selected} onSelect={setPicked} />
            </div>
          </>
        )}
      </section>
      <p className="snapshot-page__note snapshot-route__absent">{t("snapshot.route_page.not_frozen")}</p>
    </section>
  );
}
