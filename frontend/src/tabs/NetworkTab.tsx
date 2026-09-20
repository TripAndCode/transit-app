import { useRef, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { useNetworkSummary } from "../api/hooks";
import { Skeleton } from "../components/Skeleton";
import { AsyncSection } from "../components/AsyncSection";
import { Tooltip } from "../components/Tooltip";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { PageHeader } from "../components/ui/PageHeader";
import { delayColor } from "../styles/tokens";
import { useCountUp } from "../hooks/useCountUp";
import { useFlipRows } from "../hooks/useFlipRows";
import { useCappedList } from "../hooks/useCappedList";
import { useUrlState } from "../api/useUrlState";
import type { NetworkAgencyRow } from "../api/types";
import "./NetworkTab.css";

const CLAMP_NOTABLE_PCT = 1; // show a marker when ≥1% of readings were implausible (clamped)

/** Every agency's bar is drawn against this fixed span, never against the
 *  current maximum: a bar whose axis moves with the data says nothing about
 *  how one agency compares to another, or to the same agency last week.
 *  A delay past the top of the axis fills it and keeps its exact figure in
 *  the value column beside it. */
const AXIS_MAX_MIN = 6;
/** The severity threshold the product treats as "late", marked on the axis so
 *  a bar can be read against it without a legend. */
const AXIS_MARK_MIN = 5;

/** Worst first, then agencies with no data in range -- ordering by a metric
 *  puts the rows that need attention where the eye lands first, and a null
 *  delay is an absence of evidence, not a good result. */
function byDelayDescending(a: NetworkAgencyRow, b: NetworkAgencyRow): number {
  if (a.avg_delay_min == null) return b.avg_delay_min == null ? 0 : 1;
  if (b.avg_delay_min == null) return -1;
  return b.avg_delay_min - a.avg_delay_min;
}

/** The schedule version is only known for some agencies; with none there is
 *  nothing to describe, so the value renders bare rather than behind an empty
 *  bubble.
 *
 *  Pointer-only, like the `title` it replaces: the value is a metric, not a
 *  control, and giving a non-interactive element a tab stop to reach a
 *  tooltip trades one accessibility problem for another. */
function ScheduleVersionTooltip({
  label,
  children,
}: {
  label: string | null;
  children: ReactElement;
}) {
  if (label == null) return children;
  return <Tooltip label={label}>{children}</Tooltip>;
}

/** A dedicated component (not inlined in the row, which is rendered from a
 *  `.map()` callback) -- `useCountUp` is a hook, and a hook cannot be called
 *  from inside a loop callback. Animates the per-agency figure toward a new
 *  average whenever the range/filters change and the network summary
 *  refetches. */
function AgencyDelayFigure({ avgDelayMin }: { avgDelayMin: number | null }) {
  const { t } = useTranslation();
  const displayed = useCountUp(avgDelayMin ?? 0, { decimals: 1 });
  if (avgDelayMin == null) return <>—</>;
  return (
    <span style={{ color: delayColor(avgDelayMin) }}>
      {avgDelayMin >= 0 ? "+" : ""}
      {displayed.toFixed(1)}
      <span className="network-row__unit">{t("network.delay_unit")}</span>
    </span>
  );
}

function AgencyRow({
  agency,
  isCurrent,
  weightedView,
  linkSuffix,
}: {
  agency: NetworkAgencyRow;
  isCurrent: boolean;
  weightedView: boolean;
  linkSuffix: string;
}) {
  const { t } = useTranslation();
  const a = agency;
  const displayedOnTimePct = weightedView ? a.weighted_on_time_pct : a.on_time_pct;
  const axisPct =
    a.avg_delay_min == null ? 0 : Math.min(Math.max(a.avg_delay_min, 0) / AXIS_MAX_MIN, 1) * 100;
  const coverage =
    a.data_to == null
      ? t("network.no_data_in_range")
      : `${a.data_from} ${t("common.range_separator")} ${a.data_to}`;

  return (
    <div
      className={`network-row${isCurrent ? " network-row--current" : ""}`}
      data-flip-key={String(a.agency_id)}
      data-testid="network-row"
    >
      <span className="network-row__name">
        <Tooltip label={t("network.view_agency", { name: a.agency_name })}>
          <Link to={`/agencies/${a.agency_id}/overview${linkSuffix}`}>{a.agency_name}</Link>
        </Tooltip>
        {isCurrent && (
          <span data-testid="you-badge" className="network-row__you">
            {t("network.you_badge")}
          </span>
        )}
        <span className="network-row__sub">{coverage}</span>
      </span>

      <span className="network-row__value num" aria-label={t("network.col_avg_delay")}>
        <AgencyDelayFigure avgDelayMin={a.avg_delay_min} />
      </span>

      {/* Decorative: the figure it encodes is already text in the column to
          its left, so a second announcement would only repeat it. */}
      <div className="network-row__axis" aria-hidden="true">
        <div
          className="network-row__axis-fill"
          data-testid="network-axis-fill"
          style={{
            width: `${axisPct}%`,
            background: a.avg_delay_min == null ? "transparent" : delayColor(a.avg_delay_min),
          }}
        />
        <span
          className="network-row__axis-mark"
          style={{ left: `${(AXIS_MARK_MIN / AXIS_MAX_MIN) * 100}%` }}
        />
      </div>

      <span
        className="network-row__secondary num"
        aria-label={weightedView ? t("network.col_on_time_weighted") : t("network.col_on_time")}
      >
        {displayedOnTimePct == null
          ? "—"
          : `${displayedOnTimePct.toFixed(1)}%${weightedView ? t("network.on_time_weighted_suffix") : ""}`}
      </span>

      <div className="network-row__meta">
        <span>
          {t("network.col_delivered")}{" "}
          {a.service_delivered_pct == null ? "—" : `${a.service_delivered_pct.toFixed(1)}%`}
        </span>
        <ScheduleVersionTooltip
          label={
            a.static_version_id
              ? t("network.schedule_version_title", { version: a.static_version_id })
              : null
          }
        >
          <span>
            {t("network.col_vehicle_km_delivered")}{" "}
            {a.vehicle_km_delivered_pct != null
              ? `${a.vehicle_km_delivered_pct.toFixed(1)}%`
              : a.planned_trip_count != null
                ? t("network.planned_trip_count_fallback", {
                    count: a.planned_trip_count.toLocaleString(),
                  })
                : "—"}
          </span>
        </ScheduleVersionTooltip>
        <span>
          {t("network.col_samples")} {a.samples.toLocaleString()}
        </span>
        {a.clamp_pct != null && a.clamp_pct > CLAMP_NOTABLE_PCT && (
          <span>
            <span data-testid="clamp-dot" aria-hidden className="network-row__clamp-dot">
              ●
            </span>
            {a.clamp_pct.toFixed(2)}%
          </span>
        )}
        {a.is_stale && <span className="network-row__stale">{t("network.stale_badge")}</span>}
      </div>
    </div>
  );
}

export function NetworkTab() {
  const { t, i18n } = useTranslation();
  const { agencyId } = useParams();
  const currentAgencyId = agencyId ? Number(agencyId) : null;
  const [ctx, update] = useRangeContext();
  const { data, isPending, error, refetch } = useNetworkSummary(ctx);
  const [ridershipWeightedParam, setRidershipWeightedParam] = useUrlState<"1" | "0">("ridership_weighted", "0");
  const showRidershipWeighted = ridershipWeightedParam === "1";
  const rowsRef = useRef<HTMLDivElement | null>(null);

  // Absent (not just unchecked) whenever NO agency in the current list has a
  // manually-configured ridership weight -- a toggle that flips to a view
  // identical to the unweighted one would be a no-op, not a real feature.
  const ridershipWeightingAvailable = data?.agencies.some((a) => a.has_ridership_weights) ?? false;

  // Carry the full current range into each agency's Overview, matching how
  // Sidebar/ReportsTab build agency links (proper encoding; "all" dims omitted).
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";

  const ordered = data ? [...data.agencies].sort(byDelayDescending) : [];
  // Whatever can change the order: the sorted identity itself. Cheap to build
  // and exact, where a data revision counter would also fire on a refetch that
  // changed nothing.
  const orderSignal = ordered.map((a) => a.agency_id).join(",");
  useFlipRows(rowsRef, orderSignal);
  const cappedAgencies = useCappedList(ordered, 200, data?.agencies);

  return (
    <div className="network-page">
      <PageHeader
        eyebrow={t("network.eyebrow", { from: ctx.from, to: ctx.to })}
        title={t("network.title")}
        subtitle={t("network.help")}
      />
      <details className="network-howto" style={{ marginBottom: 16 }}>
        <summary>{t("network.howto_title")}</summary>
        <ul className="network-howto-list">
          <li><strong>{t("network.col_avg_delay")}</strong> — {t("network.help_avg_delay")}</li>
          <li><strong>{t("network.col_on_time")}</strong> — {t("network.help_on_time")}</li>
          {ridershipWeightingAvailable && (
            <li><strong>{t("network.ridership_weighted_toggle")}</strong> — {t("network.help_ridership_weighted")}</li>
          )}
          <li><strong>{t("network.col_delivered")}</strong> — {t("network.help_delivered")}</li>
          <li><strong>{t("network.col_vehicle_km_delivered")}</strong> — {t("network.help_vehicle_km_delivered")}</li>
          <li><strong>{t("network.col_samples")}</strong> — {t("network.help_samples")}</li>
          <li><strong>{t("network.col_feed")}</strong> — {t("network.help_feed")}</li>
          <li><strong>{t("network.col_freshness")}</strong> — {t("network.help_freshness")}</li>
          <li><strong>{t("network.col_coverage")}</strong> — {t("network.help_coverage")}</li>
        </ul>
      </details>

      <div className="network-controls">
        <label>
          {t("network.from")}{" "}
          <input type="date" lang={i18n.language} value={ctx.from} max={ctx.to} onChange={(e) => update({ from: e.target.value })} />
        </label>
        <label>
          {t("network.to")}{" "}
          <input type="date" lang={i18n.language} value={ctx.to} min={ctx.from} onChange={(e) => update({ to: e.target.value })} />
        </label>
        {ridershipWeightingAvailable && (
          <label>
            <input
              type="checkbox"
              data-testid="ridership-weighted-toggle"
              checked={showRidershipWeighted}
              onChange={(e) => setRidershipWeightedParam(e.target.checked ? "1" : "0")}
            />
            {t("network.ridership_weighted_toggle")}
          </label>
        )}
      </div>

      {/* Behind a disclosure: the aggregation rules are what you check once a
          comparison has raised a question, not what you read before making
          one. */}
      {data && (
        <details className="network-definition" style={{ marginBottom: 12 }}>
          <summary>{t("network.definition_disclosure")}</summary>
          <DefinitionMetaBlock definition={data.definition} />
        </details>
      )}

      <AsyncSection
        loading={isPending}
        error={error}
        onRetry={() => refetch()}
        data={data}
        hasContent={(summary) => summary.agencies.length > 0}
        empty={<p style={{ color: "var(--text-secondary)" }}>{t("network.empty")}</p>}
        skeleton={<Skeleton height={320} />}
      >
        {() => (
          <div className="network-rows" data-testid="network-card-list" ref={rowsRef}>
            <div className="network-row network-row--head" aria-hidden="true">
              <span>{t("network.col_agency")}</span>
              <span style={{ textAlign: "right" }}>{t("network.col_avg_delay")}</span>
              <span>{t("network.axis_caption", { max: AXIS_MAX_MIN })}</span>
              <span style={{ textAlign: "right" }}>{t("network.col_on_time")}</span>
            </div>
            {cappedAgencies.visible.map((a) => (
              <AgencyRow
                key={a.agency_id}
                agency={a}
                isCurrent={currentAgencyId != null && a.agency_id === currentAgencyId}
                // Falls back to this agency's own unweighted on_time_pct when
                // the toggle is on but THIS agency has no configured weight --
                // never blocks rendering, just can't show a weighted figure
                // that doesn't exist.
                weightedView={showRidershipWeighted && a.has_ridership_weights}
                linkSuffix={suffix}
              />
            ))}
            {cappedAgencies.remaining > 0 && (
              <button type="button" className="btn-ghost" onClick={cappedAgencies.showMore}>
                {t("common.show_more", { count: cappedAgencies.remaining })}
              </button>
            )}
          </div>
        )}
      </AsyncSection>
    </div>
  );
}
