import { useTranslation } from "react-i18next";
import { useAgencies, useTodayRouteSummary } from "../../api/hooks";
import { useAgencyId } from "../../api/useAgencyId";
import { ErrorBanner } from "../ErrorBanner";
import { formatDateTime } from "../../utils/format";

export function CompactDataStatus() {
  const id = useAgencyId();
  const { t } = useTranslation("design");
  const { data: agencies } = useAgencies();
  const query = useTodayRouteSummary(id, { autoRefresh: false });
  if (id === null) return null;
  // The newest aggregated day, the date the top bar's "Data through" shows.
  const aggregateDate = agencies?.find((a) => a.agency_id === id)?.latest_data_date ?? null;
  return <details className="focus-data-status"><summary>{t("status")}</summary>
    {query.error ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : <div>
      <p>{t("aggregateDate")}: {aggregateDate ?? "—"}</p>
      <p>{t("lastObserved")}: {query.data?.latest_captured_at ? formatDateTime(query.data.latest_captured_at) : "—"}</p>
      <p>{t("reportNote")}</p>
      <p>{t("samples")}: {query.data?.raw_samples ?? "—"}</p>
      <p>{t("excluded")}: {query.data?.clamp_count ?? "—"}</p>
    </div>}
  </details>;
}
