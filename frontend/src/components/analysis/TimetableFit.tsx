import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import type { Scope } from "../../api/scope";
import type { Band } from "../../api/timeBands";
import { useCapabilityTier } from "../../api/useCapabilityTier";
import { useIsAdmin } from "../../api/useIsAdmin";
import { EM_DASH, fmtRatioPct, formatDate, formatNumber } from "../../utils/format";
import { DefinitionMetaBlock } from "../DefinitionMetaBlock";
import { EmptyState } from "../EmptyState";
import { ErrorBanner } from "../ErrorBanner";
import { SkeletonChart } from "../Skeleton";
import { SHARED_TABLE, td, th } from "../tableStyles";
import { buildTimeBandOptions } from "../timeBandOptions";
import { segmentRuntimeNotShown } from "./notShown";
import { useTimetableFit, type TimetableFitSegment } from "./timetableFitApi";
import { bandRows, defaultBand, fitBands, stripScale } from "./timetableFitModel";

const STRIP_WIDTH = 120;
const STRIP_HEIGHT = 12;
const SECTION: CSSProperties = { display: "flex", flexDirection: "column", gap: 8 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 2, fontSize: "var(--text-sm)" };
const MUTED: CSSProperties = { color: "var(--text-secondary)", fontSize: "var(--text-sm)", margin: 0 };

function seconds(value: number | null): string {
  return value == null ? EM_DASH : formatNumber(Math.round(value));
}

function signedSeconds(value: number | null): string {
  return value == null ? EM_DASH : formatNumber(Math.round(value), { signDisplay: "exceptZero" });
}

function stopPair(s: TimetableFitSegment): string {
  return `${s.from_stop_name ?? s.from_stop_id} → ${s.to_stop_name ?? s.to_stop_id}`;
}

/** The observed spread, median to 85th percentile, as a bar, and the
 *  scheduled running time as a tick across it, on the band's one scale. */
function Strip({ segment, scale }: { segment: TimetableFitSegment; scale: number }) {
  if (scale <= 0) return null;
  const x = (value: number) => (value / scale) * STRIP_WIDTH;
  const { run_p50_sec: p50, run_p85_sec: p85, scheduled_run_sec: scheduled } = segment;
  return (
    <svg width={STRIP_WIDTH} height={STRIP_HEIGHT} aria-hidden="true" style={{ display: "block" }}>
      <line x1={0} x2={STRIP_WIDTH} y1={STRIP_HEIGHT / 2} y2={STRIP_HEIGHT / 2} style={{ stroke: "var(--border-subtle)" }} />
      {p50 != null && p85 != null && (
        <rect x={x(p50)} y={3} width={Math.max(x(p85) - x(p50), 2)} height={6} rx={2} style={{ fill: "var(--accent)" }} />
      )}
      {scheduled != null && (
        <line
          x1={x(scheduled)}
          x2={x(scheduled)}
          y1={0}
          y2={STRIP_HEIGHT}
          style={{ stroke: "var(--text-secondary)", strokeWidth: 2 }}
        />
      )}
    </svg>
  );
}

/** The route's running time per timetable segment in one time band, beside
 *  the timetable's. Feed-based estimates in the route's order: nothing is
 *  ranked and no running time is recommended from them. */
export function TimetableFit({ agencyId, ctx, route }: { agencyId: number; ctx: Scope; route: string }) {
  const { t } = useTranslation();
  const query = useTimetableFit(agencyId, ctx, route);
  const tier = useCapabilityTier(agencyId);
  const isAdmin = useIsAdmin();
  const [chosen, setChosen] = useState<Band | null>(null);
  const data = query.data;
  if (query.error) return <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />;
  if (!data) return <SkeletonChart height={240} />;
  const gap = segmentRuntimeNotShown(t, data, tier, isAdmin);
  if (gap) return <EmptyState title={gap.status} hint={gap.text} />;

  const bands = fitBands(data.segments);
  // A band the scope no longer returns falls back rather than showing nothing.
  const band = chosen != null && bands.includes(chosen) ? chosen : defaultBand(data.segments);
  const rows = band == null ? [] : bandRows(data.segments, band);
  const scale = stripScale(rows);
  const untimed = data.segments.filter((s) => s.time_band == null).length;
  const bandLabels = buildTimeBandOptions(t);
  const bandLabel = (b: Band) => bandLabels.find((o) => o.value === b)?.label ?? b;
  const summary = { date: formatDate(ctx.to), days: data.days, version: data.static_version_id };

  return (
    <section style={SECTION}>
      <p style={MUTED}>{t("timetable_fit.estimate_note")}</p>
      {data.days > 0 && (
        <p style={{ margin: 0 }}>
          {t(data.static_version_id == null ? "timetable_fit.summary_unversioned" : "timetable_fit.summary", summary)}
        </p>
      )}
      {ctx.hour != null && <p style={MUTED}>{t("timetable_fit.hour_not_applied")}</p>}
      {band == null ? (
        <EmptyState title={t("timetable_fit.none")} />
      ) : (
        <>
          {bands.length > 1 ? (
            <label style={FIELD}>
              {t("timetable_fit.band_label")}
              <select value={band} onChange={(e) => setChosen(e.target.value as Band)} style={{ alignSelf: "start" }}>
                {bands.map((b) => (
                  <option key={b} value={b}>
                    {bandLabel(b)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p style={{ margin: 0 }}>{t("timetable_fit.band_only", { band: bandLabel(band) })}</p>
          )}
          <div className="focus-table-wrap">
            <table style={SHARED_TABLE} aria-label={t("timetable_fit.table_label", { band: bandLabel(band) })}>
              <thead>
                <tr>
                  <th style={th()}>{t("timetable_fit.col.segment")}</th>
                  <th style={th()}>{t("timetable_fit.col.strip")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.scheduled")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.p50")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.p85")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.padding")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.held")}</th>
                  <th style={th({ align: "right" })}>{t("timetable_fit.col.runs")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={`${s.from_stop_id}\u0000${s.to_stop_id}`}>
                    <td style={td()}>{stopPair(s)}</td>
                    <td style={td()}>
                      <Strip segment={s} scale={scale} />
                    </td>
                    <td style={td({ align: "right" })}>{seconds(s.scheduled_run_sec)}</td>
                    <td style={td({ align: "right" })}>{seconds(s.run_p50_sec)}</td>
                    <td style={td({ align: "right" })}>{seconds(s.run_p85_sec)}</td>
                    <td style={td({ align: "right" })}>{signedSeconds(s.padding_sec)}</td>
                    <td style={td({ align: "right" })}>{fmtRatioPct(s.hold_rate)}</td>
                    <td style={td({ align: "right" })}>{formatNumber(s.samples)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={MUTED}>{t("timetable_fit.strip_legend")}</p>
          <p style={MUTED}>{t("timetable_fit.rule")}</p>
        </>
      )}
      {untimed > 0 && <p style={MUTED}>{t("timetable_fit.untimed", { n: untimed })}</p>}
      {data.terminus_early_rate != null && (
        <p style={{ margin: 0 }}>
          {t("timetable_fit.terminus", {
            rate: fmtRatioPct(data.terminus_early_rate),
            arrivals: formatNumber(data.terminus_arrivals),
          })}
        </p>
      )}
      <DefinitionMetaBlock definition={data.definition} />
    </section>
  );
}
