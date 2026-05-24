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

  return (
    <>
      <TabFilterBar />
      <div className="ov-page">
        {isPending && <Skeleton height={400} />}
        {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
        {data && data.headline.samples === 0 && (
          <EmptyState title={t("overview.empty")} />
        )}
        {data && data.headline.samples > 0 && (
          <>
            <HeroSentence
              headline={data.headline}
              sparkline_points={data.sparkline_points}
              range={{ from: ctx.from, to: ctx.to }}
            />
            <hr className="ov-divider" />
            <div className="ov-movers">
              <MoversList direction="worse" movers={data.movers.worse} />
              <MoversList direction="better" movers={data.movers.better} />
            </div>
            <hr className="ov-divider" />
            <ConcentrationBar concentration={data.concentration} />
            <hr className="ov-divider" />
            <PeakHourRibbon peak_hour={data.peak_hour} />
            <hr className="ov-divider" />
            <ServiceSplit service_split={data.service_split} />
          </>
        )}
      </div>
    </>
  );
}
