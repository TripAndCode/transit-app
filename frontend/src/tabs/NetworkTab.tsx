import { Fragment, useRef, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { scopeToQueryString, useScope } from "../api/scope";
import { useNetworkSummary } from "../api/hooks";
import { Skeleton } from "../components/Skeleton";
import { StillWorking } from "../components/StillWorking";
import { AsyncSection } from "../components/AsyncSection";
import { Tooltip } from "../components/Tooltip";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { Section } from "../components/ui/Section";
import { delayRampVar } from "../styles/tokens";
import { useCountUp } from "../hooks/useCountUp";
import { delayAxisMax, delayAxisShare } from "../components/charts/delayAxis";
import { byAgencyName, hasStopped } from "../components/agencyDirectory";
import { fixedTenth, fmtPct, formatDate, formatDateRange, formatNumber, roundTenth } from "../utils/format";
import { useFlipRows } from "../hooks/useFlipRows";
import { useCappedList } from "../hooks/useCappedList";
import { useUrlState } from "../api/useUrlState";
import type { ComparabilityThresholds, NetworkAgencyRow } from "../api/types";
import "./NetworkTab.css";

const CLAMP_NOTABLE_PCT = 1; // show a marker when ≥1% of readings were implausible (clamped)

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

/** The newest last-data day across the board, against which a row's own
 *  last day says whether its data stopped (the agency picker's rule). */
function newestDataTo(agencies: readonly NetworkAgencyRow[]): string | null {
  let newest: string | null = null;
  for (const { data_to: day } of agencies) if (day && (newest == null || day > newest)) newest = day;
  return newest;
}

/** 0 for current data, 1 for data that stopped, 2 for none: a stopped
 *  agency's figures cover fewer days than the window, so it is ranked after
 *  the agencies whose figures cover it, as the agency picker orders them. */
function dataRank(a: NetworkAgencyRow, newest: string | null): number {
  if (a.data_to == null) return 2;
  return hasStopped(a.data_to, newest) ? 1 : 0;
}

/** Half an on-time interval's width, rounded up to a tenth, so an interval
 *  the gate found wider than its limit never prints at the limit. The inner
 *  round drops float noise from the subtraction. */
function ciHalfWidth(low: number, high: number): number {
  return Math.ceil(Math.round(((high - low) / 2) * 1000) / 100) / 10;
}

/** Why coverage could not be measured, from what the row says about its plan:
 *  the planned count coverage divides by is null for a feed whose trip_ids
 *  aren't the timetable's or a plan that could not be read, and 0 where no
 *  timetable plans the days observed. */
function coverageUnknownWhy(a: NetworkAgencyRow): "unreadable" | "unmatched" | "no_plan" | "no_plan_observed" {
  if (a.delivered_missing === "unreadable") return "unreadable";
  if (a.coverage_planned_trips == null) return "unmatched";
  return a.planned_trips === 0 ? "no_plan" : "no_plan_observed";
}

/** The first check an unranked agency fails, in words, with the figure and
 *  the threshold it missed, so the row says why without a lookup. */
function unrankedReason(
  a: NetworkAgencyRow,
  limits: ComparabilityThresholds,
  t: ReturnType<typeof useTranslation>["t"],
): string | null {
  switch (a.comparable_reason) {
    case null:
      return null;
    case "no_data":
      return t("network.unranked_reason.no_data");
    case "coverage_unknown":
      return t("network.unranked_reason.coverage_unknown", {
        why: t(`network.coverage_unknown_why.${coverageUnknownWhy(a)}`),
      });
    case "coverage_low":
      return t("network.unranked_reason.coverage_low", {
        pct: a.coverage_pct == null ? "—" : fixedTenth(a.coverage_pct),
        min: formatNumber(limits.min_coverage_pct),
      });
    case "ci_wide":
      return t("network.unranked_reason.ci_wide", {
        half: a.ci_low == null || a.ci_high == null ? "—" : fixedTenth(ciHalfWidth(a.ci_low, a.ci_high)),
        max: formatNumber(limits.max_ci_half_width_pp),
      });
    case "clamp_high":
      return t("network.unranked_reason.clamp_high", {
        pct: a.clamp_pct == null ? "—" : fixedTenth(a.clamp_pct),
        max: formatNumber(limits.max_clamp_pct),
      });
  }
}

/** The gate's thresholds as the copy quotes them. */
function thresholdValues(limits: ComparabilityThresholds): { coverage: string; ci: string; clamp: string } {
  return {
    coverage: formatNumber(limits.min_coverage_pct),
    ci: formatNumber(limits.max_ci_half_width_pp),
    clamp: formatNumber(limits.max_clamp_pct),
  };
}

/** The planned figure, and every rate built on it, counts only the days a
 *  timetable plans, so a plan for part of the window counts fewer days than
 *  its neighbours'. With no plan there is nothing to qualify. */
function isPartlyTimetabled(a: NetworkAgencyRow): boolean {
  return a.planned_trips > 0 && a.covered_days < a.window_days;
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
    <span>
      {roundTenth(displayed) >= 0 ? "+" : ""}
      {fixedTenth(displayed)}
      <span className="network-row__unit">{t("network.delay_unit")}</span>
    </span>
  );
}

function AgencyRow({
  agency,
  isCurrent,
  weightedView,
  linkSuffix,
  axisMax,
  windowFrom,
  windowTo,
  newest,
  limits,
}: {
  agency: NetworkAgencyRow;
  isCurrent: boolean;
  weightedView: boolean;
  linkSuffix: string;
  axisMax: number;
  windowFrom: string;
  windowTo: string;
  newest: string | null;
  limits: ComparabilityThresholds;
}) {
  const { t } = useTranslation();
  const a = agency;
  const displayedOnTimePct = weightedView ? a.weighted_on_time_pct : a.on_time_pct;
  const axisPct = delayAxisShare(a.avg_delay_min, axisMax) * 100;
  const stopped = a.data_to != null && hasStopped(a.data_to, newest);
  // The period is the board's own heading; a row repeats it only where its
  // data covers less of it.
  const coverage =
    a.data_to == null
      ? t("network.no_data_in_range")
      : a.data_from !== windowFrom || a.data_to !== windowTo
        ? formatDateRange(a.data_from ?? a.data_to, a.data_to)
        : null;
  const planned =
    a.delivered_missing === "unreadable"
      ? t("network.planned_trips_unknown")
      : isPartlyTimetabled(a)
        ? t("network.planned_trips_partly", {
            count: formatNumber(a.planned_trips),
            covered: a.covered_days,
            window: a.window_days,
          })
        : t("network.planned_trips", { count: formatNumber(a.planned_trips) });
  const delivered =
    a.service_delivered_pct != null
      ? `${fixedTenth(a.service_delivered_pct)}%`
      : a.delivered_missing != null
        ? t("network.delivered_value_missing", { reason: t(`network.delivered_missing.${a.delivered_missing}`) })
        : "—";
  const reason = unrankedReason(a, limits, t);
  const observedCoverage =
    a.coverage_pct != null && a.observed_trips != null && a.coverage_planned_trips != null
      ? t("network.observed_coverage", {
          pct: fixedTenth(a.coverage_pct),
          observed: formatNumber(a.observed_trips),
          planned: formatNumber(a.coverage_planned_trips),
        })
      : t("network.observed_coverage_missing");
  // The interval describes the plain on-time rate, never the weighted one.
  const interval =
    !weightedView && a.on_time_pct != null && a.ci_low != null && a.ci_high != null
      ? t("network.on_time_ci", { low: fixedTenth(a.ci_low), high: fixedTenth(a.ci_high) })
      : null;

  return (
    <div
      className={`network-row${isCurrent ? " network-row--current" : ""}`}
      data-flip-key={String(a.agency_id)}
      data-testid="network-row"
    >
      <span className="network-row__name">
        <Tooltip label={t("network.view_agency", { name: a.agency_name })}>
          <Link to={`/agencies/${a.agency_id}/live${linkSuffix}`}>{a.agency_name}</Link>
        </Tooltip>
        {isCurrent && (
          <span data-testid="viewing-badge" className="network-row__viewing">
            {t("network.viewing_badge")}
          </span>
        )}
        {coverage != null && <span className="network-row__sub">{coverage}</span>}
        {stopped && a.data_to != null && newest != null && (
          <span className="network-row__sub network-row__stopped">
            {t("onboarding.stopped", { date: formatDate(a.data_to, { year: a.data_to.slice(0, 4) !== newest.slice(0, 4) }) })}
          </span>
        )}
        {reason != null && <span className="network-row__sub network-row__reason">{reason}</span>}
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
            background: a.avg_delay_min == null ? "transparent" : delayRampVar(a.avg_delay_min),
          }}
        />
        <span
          className="network-row__axis-mark"
          style={{ left: `${delayAxisShare(AXIS_MARK_MIN, axisMax) * 100}%` }}
        />
      </div>

      <span
        className="network-row__secondary num"
        aria-label={weightedView ? t("network.col_on_time_weighted") : t("network.col_on_time")}
      >
        {displayedOnTimePct == null
          ? "—"
          : `${fixedTenth(displayedOnTimePct)}%${weightedView ? t("network.on_time_weighted_suffix") : ""}`}
        {interval != null && <span className="network-row__ci"> {interval}</span>}
      </span>

      <div className="network-row__meta">
        <span>{observedCoverage}</span>
        <span>{planned}</span>
        <span>
          {t("network.col_delivered")} {delivered}
        </span>
        {/* Vehicle-km delivered is the delivered rate applied to the current
            timetable's planned vehicle-km, so without a rate there is nothing
            to add to the reason the rate already gives. */}
        {a.service_delivered_pct != null && (
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
                ? `${fixedTenth(a.vehicle_km_delivered_pct)}%`
                : t("network.vehicle_km_no_shapes")}
            </span>
          </ScheduleVersionTooltip>
        )}
        <span>
          {t("network.col_samples")} {formatNumber(a.samples)}
        </span>
        {a.clamp_pct != null && a.clamp_pct > CLAMP_NOTABLE_PCT && (
          <span>
            <span data-testid="clamp-dot" aria-hidden className="network-row__clamp-dot">
              ●
            </span>
            {fmtPct(a.clamp_pct, t)}
          </span>
        )}
        {a.is_stale && (
          <Tooltip label={t("network.help_freshness")}>
            <span
              className="network-row__stale"
              // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- plain badge, not a control; keyboard-focusable only so the tooltip explaining staleness is reachable
              tabIndex={0}
            >
              {t("network.stale_badge")}
            </span>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

export function NetworkTab() {
  const { t, i18n } = useTranslation();
  const { agencyId } = useParams();
  const currentAgencyId = agencyId ? Number(agencyId) : null;
  const [ctx, update] = useScope();
  const { data, isPending, error, refetch } = useNetworkSummary(ctx);
  const [ridershipWeightedParam, setRidershipWeightedParam] = useUrlState<"1" | "0">("ridership_weighted", "0");
  const showRidershipWeighted = ridershipWeightedParam === "1";
  const rowsRef = useRef<HTMLDivElement | null>(null);

  // Absent (not just unchecked) whenever NO agency in the current list has a
  // manually-configured ridership weight -- a toggle that flips to a view
  // identical to the unweighted one would be a no-op, not a real feature.
  const ridershipWeightingAvailable = data?.agencies.some((a) => a.has_ridership_weights) ?? false;

  // `filterQS` identifies the fetched list. Each agency link opens that
  // agency's Live view with the same range and conditions, minus the route and
  // stop filter: those codes belong to one agency and would scope another
  // agency's map to routes it may not have.
  const filterQS = scopeToQueryString(ctx);
  const linkParams = new URLSearchParams(filterQS);
  linkParams.delete("routes");
  linkParams.delete("stop");
  const linkQS = linkParams.toString();
  const suffix = linkQS ? `?${linkQS}` : "";

  const newest = data ? newestDataTo(data.agencies) : null;
  // Only the agencies the data can compare are ranked. The rest follow by
  // name, so no figure the gate refused to compare implies an order; the
  // server leaves them in agency_id order for this.
  const ranked = (data?.agencies ?? [])
    .filter((a) => a.comparable)
    .sort((a, b) => dataRank(a, newest) - dataRank(b, newest) || byDelayDescending(a, b));
  const unranked = (data?.agencies ?? [])
    .filter((a) => !a.comparable)
    .sort((a, b) => byAgencyName(a, b) || a.agency_id - b.agency_id);
  const ordered = [...ranked, ...unranked];
  // Every agency's figure, not only the shown ones, so "show more" never
  // rescales the bars already on screen.
  const axisMax = delayAxisMax(data?.agencies.map((a) => a.avg_delay_min) ?? []);
  // Whatever can change the order: the sorted identity itself. Cheap to build
  // and exact, where a data revision counter would also fire on a refetch that
  // changed nothing.
  const orderSignal = ordered.map((a) => a.agency_id).join(",");
  useFlipRows(rowsRef, orderSignal);
  // The filters the summary was fetched for identify the list: a refetch
  // under the same filters is the same list, however new its objects are.
  const cappedAgencies = useCappedList(ordered, 200, filterQS);

  return (
    <div className="network-page">
      <Section
        eyebrow={t("network.eyebrow", { range: formatDateRange(ctx.from, ctx.to) })}
        title={t("network.title")}
        description={t("network.help")}
      />
      <details className="network-howto" style={{ marginBottom: 16 }}>
        <summary>{t("network.howto_title")}</summary>
        <ul className="network-howto-list">
          <li><strong>{t("network.col_avg_delay")}</strong> — {t("network.help_avg_delay")}</li>
          <li><strong>{t("network.col_on_time")}</strong> — {t("network.help_on_time")}</li>
          <li><strong>{t("network.col_observed_coverage")}</strong> — {t("network.help_observed_coverage")}</li>
          {data && (
            <li><strong>{t("network.col_ranking")}</strong> — {t("network.help_ranking", thresholdValues(data.comparability))}</li>
          )}
          {ridershipWeightingAvailable && (
            <li><strong>{t("network.ridership_weighted_toggle")}</strong> — {t("network.help_ridership_weighted")}</li>
          )}
          <li><strong>{t("network.col_planned_trips")}</strong> — {t("network.help_planned_trips")}</li>
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

      {/* DefinitionMetaBlock is its own disclosure: the aggregation rules are
          what you check once a comparison has raised a question, not what you
          read before making one. */}
      {data && <DefinitionMetaBlock definition={data.definition} />}

      <AsyncSection
        loading={isPending}
        error={error}
        onRetry={() => refetch()}
        data={data}
        hasContent={(summary) => summary.agencies.length > 0}
        empty={<p style={{ color: "var(--text-secondary)" }}>{t("network.empty")}</p>}
        skeleton={
          <>
            <Skeleton height={320} />
            <StillWorking scope={ctx} update={update} />
          </>
        }
      >
        {(summary) => (
          <>
            <div className="network-rows" data-testid="network-card-list" ref={rowsRef}>
              <div className="network-row network-row--head" aria-hidden="true">
                <span>{t("network.col_agency")}</span>
                <span style={{ textAlign: "right" }}>{t("network.col_avg_delay")}</span>
                <span>{t("network.axis_caption", { max: axisMax })}</span>
                <span style={{ textAlign: "right" }}>{t("network.col_on_time")}</span>
              </div>
              {cappedAgencies.visible.map((a, i) => (
                <Fragment key={a.agency_id}>
                  {/* Before the first unranked row: `visible` is a prefix of `ordered`. */}
                  {i === ranked.length && (
                    <div className="network-unranked" data-testid="network-unranked">
                      <h3 className="network-unranked__title">{t("network.unranked_heading")}</h3>
                      <p className="network-unranked__criteria">
                        {t("network.unranked_criteria", thresholdValues(summary.comparability))}
                      </p>
                    </div>
                  )}
                  <AgencyRow
                    agency={a}
                    isCurrent={currentAgencyId != null && a.agency_id === currentAgencyId}
                    // Falls back to this agency's own unweighted on_time_pct when
                    // the toggle is on but THIS agency has no configured weight --
                    // never blocks rendering, just can't show a weighted figure
                    // that doesn't exist.
                    weightedView={showRidershipWeighted && a.has_ridership_weights}
                    linkSuffix={suffix}
                    axisMax={axisMax}
                    windowFrom={data?.from ?? ctx.from}
                    windowTo={data?.to ?? ctx.to}
                    newest={newest}
                    limits={summary.comparability}
                  />
                </Fragment>
              ))}
              {cappedAgencies.remaining > 0 && (
                <button type="button" className="btn-ghost" onClick={cappedAgencies.showMore}>
                  {t("common.show_more", { count: cappedAgencies.remaining })}
                </button>
              )}
            </div>
            {cappedAgencies.visible.some(isPartlyTimetabled) && (
              <p className="network-footnote" data-testid="network-plan-coverage-footnote">
                {t("network.plan_coverage_footnote")}
              </p>
            )}
            {summary.coverage_through != null && summary.coverage_through < summary.to && (
              <p className="network-footnote" data-testid="network-coverage-through-footnote">
                {t("network.coverage_through_footnote", {
                  date: formatDate(summary.coverage_through, {
                    year: summary.coverage_through.slice(0, 4) !== summary.to.slice(0, 4),
                  }),
                })}
              </p>
            )}
          </>
        )}
      </AsyncSection>
    </div>
  );
}
