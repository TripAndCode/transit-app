import { useTranslation } from "react-i18next";
import { useRoutes } from "../../api/hooks";
import { useRangeContext, type TimeBand, type DowFilter } from "../../api/rangeContext";
import { ErrorBanner } from "../ErrorBanner";
import { routeGroups, selectedGroup } from "./routeGroups";

export function PatternFilters({ agencyId, codes, onChange }: {
  agencyId: number | null; codes: string[]; onChange: (codes: string[]) => void;
}) {
  const { t } = useTranslation("design");
  const query = useRoutes(agencyId);
  const routes = query.data ?? [];
  const groups = routeGroups(routes);
  const group = selectedGroup(routes, codes);
  const options = group ? groups.find(([name]) => name === group)![1] : routes;
  return <>
    <label>{t("line")}<select value={group} disabled={query.isPending} onChange={(e) => {
      onChange(e.target.value ? [...new Set(groups.find(([name]) => name === e.target.value)![1].flatMap((r) => r.route_code ? [r.route_code] : []))] : []);
    }}><option value="">{t("allLines")}</option>{groups.map(([name]) => <option key={name} value={name}>{name}</option>)}</select></label>
    <label>{t("pattern")}<select value={codes.length === 1 ? codes[0] : ""} disabled={query.isPending} onChange={(e) => {
      onChange(e.target.value ? [e.target.value] : group ? options.flatMap((r) => r.route_code ? [r.route_code] : []) : []);
    }}><option value="">{t("allPatterns")}</option>
      {codes.filter((code) => !options.some((r) => r.route_code === code)).map((code) => <option key={code} value={code}>{code}</option>)}
      {options.map((r) => <option key={r.route_id} value={r.route_code ?? ""}>{r.route_code} · {r.route_short_name || r.route_long_name || r.route_id}</option>)}
    </select></label>
    {query.error && <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />}
  </>;
}

export function AnalysisFilters({ agencyId }: { agencyId: number | null }) {
  const { t } = useTranslation("design");
  const [ctx, update] = useRangeContext();
  return <div className="focus-filters">
    <PatternFilters agencyId={agencyId} codes={ctx.routes} onChange={(routes) => update({ routes })} />
    <label>{t("from")}<input type="date" value={ctx.from} max={ctx.to} onChange={(e) => e.target.value && update({ from: e.target.value })} /></label>
    <label>{t("to")}<input type="date" value={ctx.to} min={ctx.from} onChange={(e) => e.target.value && update({ to: e.target.value })} /></label>
    <label>{t("days")}<select value={ctx.dow} onChange={(e) => update({ dow: e.target.value as DowFilter })}>
      {(["all", "weekday", "weekend"] as const).map((v) => <option key={v} value={v}>{t(v === "all" ? "allDays" : v)}</option>)}
    </select></label>
    <label>{t("band")}<select value={ctx.time_band} onChange={(e) => update({ time_band: e.target.value as TimeBand })}>
      {(["all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"] as const).map((v) => <option key={v} value={v}>{t(v === "all" ? "allHours" : v)}</option>)}
    </select></label>
    {ctx.service !== "all" && <button type="button" onClick={() => update({ service: "all" })}>{t(ctx.service === "平日" ? "weekday" : "weekend")} ×</button>} {/* i18n-ignore: query contract */}
  </div>;
}
