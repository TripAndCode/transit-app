import { useState } from "react";
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

type Draft = { routes: string[]; from: string; to: string; dow: DowFilter; time_band: TimeBand };

/** The analysis screen's filters, committed on Apply rather than per keystroke.
 *
 * Deferred for the same reason the operations map defers (see
 * `tabs/map/FilterDock.tsx`), only more sharply here: this screen renders
 * only when exactly one pattern is selected, and narrowing to one runs
 * *through* a multi-code state, because choosing a line selects every code
 * under it before the pattern select cuts it to one. Committing each step
 * pushed the screen into its "choose a route and a service pattern" empty
 * state mid-selection and re-ran every query on the way back out.
 *
 * Draft state is seeded from the live context and re-seeded whenever the
 * context changes underneath — a preset, a drilldown link, a browser Back —
 * using the render-adjust pattern rather than an effect, matching
 * `TabFilterBar`.
 */
export function AnalysisFilters({ agencyId }: { agencyId: number | null }) {
  const { t } = useTranslation("design");
  const [ctx, update] = useRangeContext();

  const live: Draft = {
    routes: ctx.routes, from: ctx.from, to: ctx.to, dow: ctx.dow, time_band: ctx.time_band,
  };
  const [draft, setDraft] = useState<Draft>(live);
  const liveKey = `${ctx.routes.join(",")}|${ctx.from}|${ctx.to}|${ctx.dow}|${ctx.time_band}`;
  const [prevLiveKey, setPrevLiveKey] = useState(liveKey);
  if (prevLiveKey !== liveKey) {
    setPrevLiveKey(liveKey);
    setDraft(live);
  }

  // Routes compare as sets: `PatternFilters` rebuilds a whole group's code
  // list on a line change, so an unchanged selection can return in a
  // different order and would otherwise read as pending forever.
  const dirty =
    !sameCodes(draft.routes, ctx.routes) ||
    draft.from !== ctx.from ||
    draft.to !== ctx.to ||
    draft.dow !== ctx.dow ||
    draft.time_band !== ctx.time_band;

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  return (
    // A real <form>, so Enter from any focused control commits the way a
    // keyboard user expects without a hand-rolled key handler.
    <form
      className="focus-filters"
      onSubmit={(e) => { e.preventDefault(); if (dirty) update(draft); }}
    >
      <PatternFilters agencyId={agencyId} codes={draft.routes} onChange={(routes) => set("routes", routes)} />
      <label>{t("from")}<input type="date" value={draft.from} max={draft.to} onChange={(e) => e.target.value && set("from", e.target.value)} /></label>
      <label>{t("to")}<input type="date" value={draft.to} min={draft.from} onChange={(e) => e.target.value && set("to", e.target.value)} /></label>
      <label>{t("days")}<select value={draft.dow} onChange={(e) => set("dow", e.target.value as DowFilter)}>
        {(["all", "weekday", "weekend"] as const).map((v) => <option key={v} value={v}>{t(v === "all" ? "allDays" : v)}</option>)}
      </select></label>
      <label>{t("band")}<select value={draft.time_band} onChange={(e) => set("time_band", e.target.value as TimeBand)}>
        {(["all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"] as const).map((v) => <option key={v} value={v}>{t(v === "all" ? "allHours" : v)}</option>)}
      </select></label>
      {/* Clearing the service chip commits immediately: it is a discard, not
          a selection being built up, and leaving it pending would mean
          pressing Apply to undo something. */}
      {ctx.service !== "all" && <button type="button" onClick={() => update({ service: "all" })}>{t(ctx.service === "平日" ? "weekday" : "weekend")} ×</button>} {/* i18n-ignore: query contract */}
      {dirty && (
        <>
          <button type="submit" className="focus-filters__apply">{t("apply")}</button>
          {/* The button appearing is the sighted signal; this is the same
              fact for a screen reader. */}
          <p className="focus-filters__pending" role="status">{t("pendingChanges")}</p>
        </>
      )}
    </form>
  );
}

function sameCodes(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const seen = new Set(b);
  return a.every((code) => seen.has(code));
}
