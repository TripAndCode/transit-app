import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useOverviewSummary, usePeakHourBreakdown } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { useJumpToLatestDataRange } from "../api/defaultRangeAnchor";
import { useRangeContext } from "../api/rangeContext";
import { useUrlPatch, useUrlState } from "../api/useUrlState";
import { ConcentrationBar } from "../components/ConcentrationBar";
import { EmptyState } from "../components/EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { AsyncSection } from "../components/AsyncSection";
import { OverviewHeroRow } from "../components/OverviewHeroRow";
import { OverviewModal } from "../components/OverviewModal";
import { PeakHourModal } from "../components/PeakHourModal";
import { PeakHourRibbon } from "../components/PeakHourRibbon";
import { RevealSection } from "../components/overview/RevealSection";
import { RoutesToCheckList } from "../components/RoutesToCheckList";
import { ServiceSplit } from "../components/ServiceSplit";
import { SkeletonKpiRow, SkeletonTable } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

import "../styles/overview.css";

type OpenCard = "concentration" | "peak_hour" | "service_split" | null;

export function OverviewTab() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const [ctx, update] = useRangeContext();
  const jumpToLatestData = useJumpToLatestDataRange(agencyId);
  const query = useOverviewSummary(agencyId, ctx);
  const { data, isPending, error, refetch } = query;
  const [open, setOpen] = useState<OpenCard>(null);
  // Two string keys rather than one JSON-shaped one, consistent with the
  // route-analysis stop selection: `peak_dow` is only ever written alongside
  // `peak_hour`, so its presence/absence stays a plain empty-string default.
  const [peakHourParam] = useUrlState<string>("peak_hour", "");
  const [peakDowParam] = useUrlState<string>("peak_dow", "");
  const patchUrl = useUrlPatch();
  const peakHourSel = peakHourParam
    ? { hour: Number(peakHourParam), dow: peakDowParam ? Number(peakDowParam) : null }
    : null;
  function setPeakHourSel(next: { hour: number; dow: number | null } | null) {
    patchUrl({
      peak_hour: next ? String(next.hour) : null,
      peak_dow: next?.dow != null ? String(next.dow) : null,
    });
  }
  const peakBreakdown = usePeakHourBreakdown(
    agencyId,
    peakHourSel?.hour ?? null,
    peakHourSel?.dow ?? null,
  );

  // Nothing here is answerable without an agency, and every query above is
  // already disabled for a null id.
  if (agencyId == null) return null;

  // movers is intentionally excluded here: since the retired MoversList/
  // HeroSentence removal, movers no longer drives any main-view content
  // (it's only consumed inside ConcentrationBar). Checking it would let an
  // agency with movers but no other signal skip EmptyState and render a
  // hero row of "—" plus an empty routes list and no revealed sections.
  // peak_hour is excluded for the same reason, but structurally: it reads
  // agg_route_hour, a fixed analyze-period rollup with no date column (see
  // pipeline/reports/overview.py's _peak_hour docstring), so it ignores
  // ctx's date range entirely and stays non-null for any range once an
  // agency has ever had data — it can never signal "no data in THIS range".
  const hasAnyData = (summary: NonNullable<typeof data>) =>
    summary.headline.samples > 0 ||
    summary.concentration.top_routes.length > 0 ||
    Object.keys(summary.service_split).length > 0;

  const modalTitleKey: Record<Exclude<OpenCard, null>, string> = {
    concentration: "overview.modal.concentration",
    peak_hour: "overview.modal.peak_hour",
    service_split: "overview.modal.service_split",
  };

  return (
    <>
      <TabFilterBar />
      <div className="ov-page">
        <AsyncSection
          loading={isPending}
          error={error}
          onRetry={() => refetch()}
          data={data}
          hasContent={hasAnyData}
          empty={
            <EmptyState
              title={t("overview.empty")}
              reasons={buildFilterCtxReasons(ctx, t)}
              recoveries={buildFilterCtxRecoveries({
                ctx,
                onClearRoutes: () => update({ routes: null }),
                onResetService: () => update({ service: "all" }),
                jumpToLatestData,
                t,
              })}
            />
          }
          skeleton={
            <>
              <SkeletonKpiRow />
              <SkeletonTable rows={5} />
            </>
          }
        >
          {(data) => (
          <>
            <OverviewHeroRow
              headline={data.headline}
              delayedCount={data.top_delayed.delayed_count}
              agencyId={agencyId}
              sparklinePoints={data.sparkline_points}
              peakHour={data.peak_hour}
              concentration={data.concentration}
            />
            <RoutesToCheckList routes={data.top_delayed.routes} />
            {data.concentration.top_routes.length > 0 && (
              <RevealSection index={0}>
                <ConcentrationBar
                  concentration={data.concentration}
                  movers={data.movers}
                  onClick={() => setOpen("concentration")}
                />
              </RevealSection>
            )}
            {data.peak_hour != null && (
              <RevealSection index={1}>
                <PeakHourRibbon
                  peak_hour={data.peak_hour}
                  onClick={() => setOpen("peak_hour")}
                  onHourClick={(hour) => setPeakHourSel({ hour, dow: null })}
                />
              </RevealSection>
            )}
            {Object.keys(data.service_split).length > 0 && (
              <RevealSection index={2}>
                <ServiceSplit
                  service_split={data.service_split}
                  onClick={() => setOpen("service_split")}
                />
              </RevealSection>
            )}
          </>
          )}
        </AsyncSection>
      </div>

      <OverviewModal
        isOpen={open !== null}
        onClose={() => setOpen(null)}
        title={open !== null ? t(modalTitleKey[open]) : ""}
      >
        {data && open === "concentration" && (
          <ConcentrationBar
            concentration={data.concentration}
            movers={data.movers}
            limit={20}
            variant="modal"
          />
        )}
        {data && open === "peak_hour" && (
          <PeakHourRibbon
            peak_hour={data.peak_hour}
            peak_hour_weekday={data.peak_hour_weekday ?? null}
            peak_hour_weekend={data.peak_hour_weekend ?? null}
            variant="modal"
          />
        )}
        {data && open === "service_split" && (
          <ServiceSplit
            service_split={data.service_split}
            daily={data.service_split_daily ?? []}
            variant="modal"
          />
        )}
      </OverviewModal>
      {peakHourSel != null && (
        <PeakHourModal
          data={peakBreakdown.data ?? null}
          loading={peakBreakdown.isLoading}
          onClose={() => setPeakHourSel(null)}
        />
      )}
    </>
  );
}
