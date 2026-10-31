/**
 * Why's analyses: the dwell/running split, and beside it rain against delay
 * and headway quality. The ones with something to show come first, in full.
 * Each one that can't be shown is listed once below them, with a status and
 * the sentence that gives its true cause, instead of an empty card.
 */
import { useId } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useHeadwayQuality, useWeatherDelay } from "../../api/hooks";
import { scopeToQueryString, useScope } from "../../api/scope";
import type { DwellRunPayload } from "../../api/types";
import { useAgencyId } from "../../api/useAgencyId";
import { useJumpToLatestDataRange } from "../../api/latestDataWindow";
import { useRouteNames } from "../../api/useRouteNames";
import { useCappedList } from "../../hooks/useCappedList";
import { destHref, reportHref } from "../../routes/destinations";
import { formatNumber } from "../../utils/format";
import { EmptyState } from "../EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../emptyStateRecoveries";
import { HeadwayQualityPanel } from "../HeadwayQualityPanel";
import { RouteLabel } from "../RouteLabel";
import { SHARED_TABLE, th, td } from "../tableStyles";
import { WeatherDelayPanel } from "../WeatherDelayPanel";
import { dwellNotShown, headwayNotShown, weatherNotShown, type NotShown } from "./notShown";
import { reportLabel } from "./reportGroups";

type NotShownItem = NotShown & { key: string; name: string };

export function WhyAnalyses({ aid, payload }: { aid: number; payload: DwellRunPayload | undefined }) {
  const { t } = useTranslation();
  const [ctx] = useScope();
  const weather = useWeatherDelay(aid, ctx, true);
  const headway = useHeadwayQuality(aid, ctx, true);
  const dwellGap = dwellNotShown(t, payload);
  const weatherGap = weather.data ? weatherNotShown(t, weather.data) : null;
  const headwayGap = headway.data ? headwayNotShown(t, headway.data) : null;
  const notShown = [
    dwellGap && { key: "dwell_run", name: reportLabel(t, "dwell_run"), ...dwellGap },
    weatherGap && { key: "weather", name: t("reports.weather_delay.title"), ...weatherGap },
    headwayGap && { key: "headway", name: t("reports.headway_quality.title"), ...headwayGap },
  ].filter((item): item is NotShownItem => item != null);

  return (
    <>
      {!dwellGap && <DwellRunBlock payload={payload} />}
      {!weatherGap && <WeatherDelayPanel aid={aid} ctx={ctx} />}
      {!headwayGap && <HeadwayQualityPanel aid={aid} ctx={ctx} />}
      {notShown.length > 0 && <NotShownList items={notShown} aid={aid} offerElsewhere={dwellGap != null} />}
    </>
  );
}

/** Without the split, the delay itself can still be looked at by time of day
 *  and by kind of day, so those two stay one click away. */
function NotShownList({ items, aid, offerElsewhere }: { items: NotShownItem[]; aid: number; offerElsewhere: boolean }) {
  const { t } = useTranslation();
  const [ctx] = useScope();
  const navigate = useNavigate();
  const id = useId();
  const search = `?${scopeToQueryString(ctx)}`;
  return (
    <section className="why-not-shown" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className="why-not-shown__title">
        {t("reports.why.not_shown_title")}
      </h3>
      <ul className="why-not-shown__list">
        {items.map((item) => (
          <li key={item.key} className="why-not-shown__item" aria-labelledby={`${id}-${item.key}`}>
            <div className="why-not-shown__head">
              <span id={`${id}-${item.key}`} className="why-not-shown__name">
                {item.name}
              </span>
              <span className="caveat-badge">{item.status}</span>
            </div>
            <p className="why-not-shown__text">{item.text}</p>
          </li>
        ))}
      </ul>
      {offerElsewhere && (
        <div className="why-not-shown__elsewhere">
          <button type="button" className="btn-ghost" onClick={() => navigate(destHref(aid, "time", search))}>
            {t("reports.dwell_run.see_when")}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate(reportHref(aid, "compare_ranking", search))}>
            {t("reports.dwell_run.compare_day_types")}
          </button>
        </div>
      )}
    </section>
  );
}

function DwellRunBlock({ payload }: { payload: DwellRunPayload | undefined }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const routeNames = useRouteNames(id);
  const [ctx, update] = useScope();
  // The agency and filters the report was fetched for identify the list: a
  // refetch under the same ones is the same list, however new its objects are.
  const cappedRoutes = useCappedList(payload?.routes ?? [], 200, `${id ?? "none"}:${scopeToQueryString(ctx)}`);
  const jumpToLatestData = useJumpToLatestDataRange(id);

  if (payload && !payload.time_band_supported) {
    return <EmptyState title={t("reports.dwell_run.time_band_unsupported")} />;
  }
  if (!payload || payload.routes.length === 0) {
    return (
      <EmptyState
        title={t("reports.no_data.title")}
        hint={t("reports.no_data.hint")}
        reasons={buildFilterCtxReasons(ctx, t, routeNames.format)}
        recoveries={buildFilterCtxRecoveries({
          ctx,
          onClearRoutes: () => update({ routes: null }),
          onResetService: () => update({ service: "all" }),
          jumpToLatestData,
          t,
        })}
      />
    );
  }

  const fmtSec = (v: number | null): string => (v == null ? "—" : `${v.toFixed(0)}${t("common.unit_sec")}`);
  const fmtSamples = (v: number): string => formatNumber(v);

  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <table style={SHARED_TABLE}>
        <thead>
          <tr style={{ background: "var(--bg-soft)" }}>
            <th style={th({ width: 40 })}>#</th>
            <th style={th()}>{t("common.route")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_avg")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_p50")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_p90")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_samples")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_avg")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_p50")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_p90")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_samples")}</th>
          </tr>
        </thead>
        <tbody>
          {cappedRoutes.visible.map((r, i) => (
            <tr key={r.route_code} style={{ borderTop: "1px solid var(--border-soft)" }}>
              <td style={{ ...td({ align: "right" }), color: "var(--text-tertiary)" }}>{i + 1}</td>
              <td style={{ ...td(), fontWeight: 500 }}><RouteLabel code={r.route_code} names={routeNames} compact /></td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_avg_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_p50_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_p90_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSamples(r.dwell_samples)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_avg_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_p50_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_p90_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSamples(r.run_samples)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {cappedRoutes.remaining > 0 && (
        <button type="button" className="btn-ghost" onClick={cappedRoutes.showMore}>
          {t("common.show_more", { count: cappedRoutes.remaining })}
        </button>
      )}
    </div>
  );
}
