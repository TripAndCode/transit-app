// frontend/src/tabs/OverviewTab.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import { useOverviewSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { ConcentrationBar } from "../components/ConcentrationBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HeroSentence } from "../components/HeroSentence";
import { MoversList } from "../components/OverviewMoversList";
import { OverviewModal } from "../components/OverviewModal";
import { PeakHourRibbon } from "../components/PeakHourRibbon";
import { ServiceSplit } from "../components/ServiceSplit";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

import "../styles/overview.css";

function useAgencyId(): number | null {
  const { agencyId } = useParams();
  return agencyId ? Number(agencyId) : null;
}

type OpenCard =
  | "hero"
  | "movers_worse"
  | "movers_better"
  | "concentration"
  | "peak_hour"
  | "service_split"
  | null;

export function OverviewTab() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const [ctx] = useRangeContext();
  const query = useOverviewSummary(agencyId, ctx);
  const { data, isPending, error, refetch } = query;
  const [open, setOpen] = useState<OpenCard>(null);

  const hasAnyData =
    !!data && (
      data.headline.samples > 0 ||
      data.concentration.top_routes.length > 0 ||
      data.peak_hour != null ||
      Object.keys(data.service_split).length > 0 ||
      data.movers.worse.length > 0 ||
      data.movers.better.length > 0
    );

  const modalTitleKey: Record<Exclude<OpenCard, null>, string> = {
    hero: "overview.modal.hero",
    movers_worse: "overview.modal.movers_worse",
    movers_better: "overview.modal.movers_better",
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
            {data.headline.samples > 0 && (
              <HeroSentence
                headline={data.headline}
                sparkline_points={data.sparkline_points}
                onClick={() => setOpen("hero")}
              />
            )}
            {(data.movers.worse.length > 0 || data.movers.better.length > 0) && (
              <div className="ov-movers">
                {data.movers.worse.length > 0 && (
                  <MoversList
                    direction="worse"
                    movers={data.movers.worse}
                    onClick={() => setOpen("movers_worse")}
                  />
                )}
                {data.movers.better.length > 0 && (
                  <MoversList
                    direction="better"
                    movers={data.movers.better}
                    onClick={() => setOpen("movers_better")}
                  />
                )}
              </div>
            )}
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
              />
            )}
            {Object.keys(data.service_split).length > 0 && (
              <ServiceSplit
                service_split={data.service_split}
                onClick={() => setOpen("service_split")}
              />
            )}
          </>
        )}
      </div>

      <OverviewModal
        isOpen={open !== null}
        onClose={() => setOpen(null)}
        title={open !== null ? t(modalTitleKey[open]) : ""}
      >
        {data && open === "hero" && (
          <HeroSentence
            headline={data.headline}
            sparkline_points={data.sparkline_points}
            variant="modal"
          />
        )}
        {data && open === "movers_worse" && (
          <MoversList
            direction="worse"
            movers={data.movers.worse}
            limit={10}
            variant="modal"
          />
        )}
        {data && open === "movers_better" && (
          <MoversList
            direction="better"
            movers={data.movers.better}
            limit={10}
            variant="modal"
          />
        )}
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
    </>
  );
}
