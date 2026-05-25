// frontend/src/tabs/OverviewTab.tsx
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import { useOverviewSummary } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { ConcentrationBar } from "../components/ConcentrationBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HeroSentence } from "../components/HeroSentence";
import { MoversList } from "../components/MoversList";
import { PeakHourRibbon } from "../components/PeakHourRibbon";
import { ServiceSplit } from "../components/ServiceSplit";
import { Skeleton } from "../components/Skeleton";
import { TabFilterBar } from "../components/TabFilterBar";

import "../styles/overview.css";

function useAgencyId(): number | null {
  const { agencyId } = useParams();
  return agencyId ? Number(agencyId) : null;
}

export function OverviewTab() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const [ctx] = useRangeContext();
  const query = useOverviewSummary(agencyId, ctx);
  const { data, isPending, error, refetch } = query;

  const hasAnyData =
    !!data && (
      data.headline.samples > 0 ||
      data.concentration.top_routes.length > 0 ||
      data.peak_hour != null ||
      Object.keys(data.service_split).length > 0 ||
      data.movers.worse.length > 0 ||
      data.movers.better.length > 0
    );

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
              />
            )}
            {(data.movers.worse.length > 0 || data.movers.better.length > 0) && (
              <div className="ov-movers">
                {data.movers.worse.length > 0 && (
                  <MoversList direction="worse" movers={data.movers.worse} />
                )}
                {data.movers.better.length > 0 && (
                  <MoversList direction="better" movers={data.movers.better} />
                )}
              </div>
            )}
            {data.concentration.top_routes.length > 0 && (
              <ConcentrationBar concentration={data.concentration} />
            )}
            {data.peak_hour != null && (
              <PeakHourRibbon peak_hour={data.peak_hour} />
            )}
            {Object.keys(data.service_split).length > 0 && (
              <ServiceSplit service_split={data.service_split} />
            )}
          </>
        )}
      </div>
    </>
  );
}
