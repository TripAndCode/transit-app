// frontend/src/tabs/OverviewTab.tsx
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import { useOverviewSummary, usePeakHourBreakdown } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { ConcentrationBar } from "../components/ConcentrationBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { OverviewHeroRow } from "../components/OverviewHeroRow";
import { OverviewModal } from "../components/OverviewModal";
import { PeakHourModal } from "../components/PeakHourModal";
import { PeakHourRibbon } from "../components/PeakHourRibbon";
import { RoutesToCheckList } from "../components/RoutesToCheckList";
import { ServiceSplit } from "../components/ServiceSplit";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

import "../styles/overview.css";

// Keeps maplibre-gl out of Overview's default chunk, matching main.tsx's
// existing lazy-split convention for MapTab itself.
const OverviewMiniMap = lazy(() =>
  import("../components/OverviewMiniMap").then((m) => ({ default: m.OverviewMiniMap })),
);

function useAgencyId(): number | null {
  const { agencyId } = useParams();
  return agencyId ? Number(agencyId) : null;
}

type OpenCard = "concentration" | "peak_hour" | "service_split" | null;

export function OverviewTab() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const [ctx] = useRangeContext();
  const query = useOverviewSummary(agencyId, ctx);
  const { data, isPending, error, refetch } = query;
  const [open, setOpen] = useState<OpenCard>(null);
  const [peakHourSel, setPeakHourSel] = useState<{
    hour: number;
    dow: number | null;
  } | null>(null);
  const peakBreakdown = usePeakHourBreakdown(
    agencyId,
    peakHourSel?.hour ?? null,
    peakHourSel?.dow ?? null,
  );

  // movers is intentionally excluded here: since the retired MoversList/
  // HeroSentence removal, movers no longer drives any main-view content
  // (it's only consumed inside the collapsed ConcentrationBar). Checking
  // it would let an agency with movers but no other signal skip
  // EmptyState and render a hero row of "—"/an empty routes list/a
  // details toggle that reveals nothing.
  // peak_hour is excluded for the same reason, but structurally: it reads
  // agg_route_hour, a fixed analyze-period rollup with no date column (see
  // pipeline/reports/overview.py's _peak_hour docstring), so it ignores
  // ctx's date range entirely and stays non-null for any range once an
  // agency has ever had data — it can never signal "no data in THIS range".
  const hasAnyData =
    !!data && (
      data.headline.samples > 0 ||
      data.concentration.top_routes.length > 0 ||
      Object.keys(data.service_split).length > 0
    );

  const modalTitleKey: Record<Exclude<OpenCard, null>, string> = {
    concentration: "overview.modal.concentration",
    peak_hour: "overview.modal.peak_hour",
    service_split: "overview.modal.service_split",
  };

  return (
    <>
      <TabFilterBar />
      <div className="ov-page">
        {isPending && <Skeleton height={400} />}
        {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
        {data && !hasAnyData && (
          <EmptyState title={t("overview.empty")} />
        )}
        {data && hasAnyData && (
          <>
            <OverviewHeroRow
              headline={data.headline}
              delayedCount={data.top_delayed.delayed_count}
              agencyId={agencyId!}
            />
            <Suspense fallback={<Skeleton height={180} style={{ marginBottom: 24 }} />}>
              <OverviewMiniMap agencyId={agencyId!} ctx={ctx} />
            </Suspense>
            <RoutesToCheckList routes={data.top_delayed.routes} />
            <details className="ov-details">
              <summary className="ov-details-summary">{t("overview.details_toggle")}</summary>
              {data.concentration.top_routes.length > 0 && (
                <ConcentrationBar
                  concentration={data.concentration}
                  movers={data.movers}
                  onClick={() => setOpen("concentration")}
                />
              )}
              {data.peak_hour != null && (
                <PeakHourRibbon
                  peak_hour={data.peak_hour}
                  onClick={() => setOpen("peak_hour")}
                  onHourClick={(hour) => setPeakHourSel({ hour, dow: null })}
                />
              )}
              {Object.keys(data.service_split).length > 0 && (
                <ServiceSplit
                  service_split={data.service_split}
                  onClick={() => setOpen("service_split")}
                />
              )}
            </details>
          </>
        )}
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
