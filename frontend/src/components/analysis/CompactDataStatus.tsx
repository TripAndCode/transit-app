import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../../api/hooks";
import { ErrorBanner } from "../ErrorBanner";
import { formatDateTime } from "../../utils/format";

export function CompactDataStatus() {
  const { agencyId } = useParams();
  const { t } = useTranslation("design");
  const query = useTodayRouteSummary(agencyId ? Number(agencyId) : null, { autoRefresh: false });
  if (!agencyId) return null;
  return <details className="focus-data-status"><summary>{t("status")}</summary>
    {query.error ? <ErrorBanner error={query.error} onRetry={() => void query.refetch()} /> : <div>
      <p>{t("aggregateDate")}: {query.data?.date ?? "—"}</p>
      <p>{t("lastObserved")}: {query.data?.latest_captured_at ? formatDateTime(query.data.latest_captured_at) : "—"}</p>
      <p>{t("reportNote")}</p>
      <p>{t("samples")}: {query.data?.raw_samples ?? "—"}</p>
      <p>{t("excluded")}: {query.data?.clamp_count ?? "—"}</p>
    </div>}
  </details>;
}
