import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { useAgencies, useOverviewSummary } from "../api/hooks";
import type { OverviewSummary } from "../api/types";
import { clearLastAgency } from "../api/lastAgency";
import { useAgencyId } from "../api/useAgencyId";
import { useRouteNames } from "../api/useRouteNames";
import { useJumpToLatestDataRange } from "../api/latestDataWindow";
import { scopeToQueryString, useScope, type Scope } from "../api/scope";
import { scopeWindow } from "../api/onTimeDefault";
import { ConcentrationBar } from "../components/ConcentrationBar";
import { EmptyState } from "../components/EmptyState";
import { FirstRunTour } from "../components/FirstRunTour";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { AsyncSection } from "../components/AsyncSection";
import { OverviewHeroRow, type HeroFact } from "../components/OverviewHeroRow";
import { OverviewModal } from "../components/OverviewModal";
import { PeakHourSummary } from "../components/PeakHourSummary";
import { RevealSection } from "../components/overview/RevealSection";
import { RoutesPreview } from "../components/overview/RoutesPreview";
import { ServiceSplit } from "../components/ServiceSplit";
import { SkeletonKpiRow, SkeletonTable } from "../components/Skeleton";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { ScopeSentence } from "../components/scope/ScopeSentence";
import { reportHref } from "../routes/destinations";
import { formatDateRange } from "../utils/format";
import { prefersReducedMotion } from "../utils/motion";

import "../styles/overview.css";

type OpenCard = "concentration" | null;

/** The headline's window as text, shared by the hero and the routes-to-check
 *  header so both name it the same way. */
function headlineRange(summary: OverviewSummary): string {
  return formatDateRange(summary.headline.window_from, summary.headline.window_to, { year: false });
}

/** The period the period-wide figures count, as text. */
function periodRange(summary: OverviewSummary): string {
  return formatDateRange(summary.period_from, summary.period_to, { year: false });
}

/** The scope a figure was counted over: the screen's filters on the days the
 *  API names for it, so a link carrying it opens the same figures. */
function countedScope(ctx: Scope, from: string, to: string): Scope {
  return { ...ctx, from, to };
}

export function OverviewTab() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const routeNames = useRouteNames(agencyId);
  const [ctx, update] = useScope();
  const jumpToLatestData = useJumpToLatestDataRange(agencyId);
  const navigate = useNavigate();
  const agencies = useAgencies();
  const agency = agencies.data?.find((a) => a.agency_id === agencyId);
  const query = useOverviewSummary(agencyId, ctx);
  const { data, isPending, error, refetch } = query;
  const [open, setOpen] = useState<OpenCard>(null);
  // The tour's place for this visit, kept here because the figures it is
  // mounted with are replaced while a changed scope loads.
  const [tourStep, setTourStep] = useState(0);
  const [tourLater, setTourLater] = useState(false);
  const routesToCheckRef = useRef<HTMLDivElement>(null);

  // The routes-to-check preview is inline rather than behind a modal, so its
  // fact row brings it into view and moves focus there, where a keyboard
  // reader continues into its route links.
  function openFact(fact: HeroFact) {
    if (fact !== "late_routes") {
      setOpen(fact);
      return;
    }
    const list = routesToCheckRef.current;
    if (!list) return;
    list.scrollIntoView?.({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" }); // jsdom has no scrollIntoView
    list.focus({ preventScroll: true });
  }

  // Nothing here is answerable without an agency, and every query above is
  // already disabled for a null id.
  if (agencyId == null) return null;

  // movers is intentionally excluded here: it drives no main-view content
  // (it's only consumed inside ConcentrationBar). Checking it would let an
  // agency with movers but no other signal skip EmptyState and render a
  // hero row of "—" plus an empty routes list and no revealed sections.
  // peak_hour adds nothing to the check: it reads the same window as
  // concentration, so it has data exactly when concentration does.
  const hasAnyData = (summary: NonNullable<typeof data>) =>
    summary.headline.samples > 0 ||
    summary.concentration.top_routes.length > 0 ||
    summary.service_split.some((entry) => entry.samples > 0);

  // Whether the agency was ever collected decides which empty state is true,
  // so the generic "nothing in this range" waits for the agency list.
  const emptyState = agencies.isPending ? (
    <SkeletonTable rows={3} />
  ) : agency && !agency.latest_data_date ? (
    <EmptyState
      title={t("overview.never_collected", { agency: agency.agency_name })}
      recoveries={[
        {
          label: t("overview.choose_another_agency"),
          onClick: () => {
            clearLastAgency();
            navigate("/");
          },
        },
      ]}
    />
  ) : (
    <EmptyState
      title={t("overview.empty")}
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


  return (
    <ScopeHeaderProvider>
      {/* The heading and the sentence share the page's column, so they line
          up with each other and with the cards below. */}
      <div className="ov-page-head" data-tour="pulse-scope">
        <ScopePageHeader title={t("nav.pulse")} />
        <ScopeSentence applied={query.data?.scope_applied} />
      </div>
      <div className="ov-page">
        <AsyncSection
          loading={isPending}
          error={error}
          onRetry={() => refetch()}
          data={data}
          hasContent={hasAnyData}
          empty={emptyState}
          skeleton={
            <>
              <SkeletonKpiRow />
              <SkeletonTable rows={5} />
            </>
          }
        >
          {(data) => {
          const periodQuery = scopeToQueryString(countedScope(ctx, data.period_from, data.period_to));
          return (
          <>
            {/* Inside the figures' branch, so every step's anchor is on the
                page whenever the tour is: an anchor that never appears would
                leave its focus trap holding Tab on a page showing no tour. */}
            {!tourLater && (
              <FirstRunTour
                onTime={scopeWindow(ctx)}
                step={tourStep}
                onStep={setTourStep}
                onLater={() => setTourLater(true)}
              />
            )}
            <OverviewHeroRow
              headline={data.headline}
              severeCount={data.top_delayed.band_counts.severe.count}
              severeThresholdMin={data.top_delayed.band_counts.severe.threshold_min}
              routeCount={data.top_delayed.route_count}
              minSamples={data.top_delayed.min_samples}
              agencyId={agencyId}
              sparklinePoints={data.sparkline_points}
              periodAvgMin={data.period_avg_min ?? null}
              headlineRange={headlineRange(data)}
              periodRange={periodRange(data)}
              peakHour={data.peak_hour}
              concentration={data.concentration}
              daily={data.service_split_daily ?? []}
              trendHref={reportHref(agencyId, "trend", periodQuery)}
              onOpenFact={openFact}
            />
            <div ref={routesToCheckRef} className="ov-routes-anchor" tabIndex={-1}>
              <RoutesPreview
                agencyId={agencyId}
                scope={countedScope(ctx, data.headline.window_from, data.headline.window_to)}
                range={headlineRange(data)}
              />
            </div>
            {/* The stagger index counts the sections that actually render.
                Any of these can be absent for an agency, and a fixed index
                would hand whichever one appears first a delay with nothing
                in front of it to follow. */}
            {[
              data.concentration.top_routes.length > 0
                ? {
                    key: "concentration",
                    node: (
                      <ConcentrationBar
                        concentration={data.concentration}
                        periodRange={periodRange(data)}
                        movers={data.movers}
                        onClick={() => setOpen("concentration")}
                      />
                    ),
                  }
                : null,
              data.peak_hour != null
                ? {
                    key: "peak_hour",
                    node: (
                      <PeakHourSummary peak_hour={data.peak_hour} timeHref={reportHref(agencyId, "trend", periodQuery)} />
                    ),
                  }
                : null,
              data.service_split.some((entry) => entry.samples > 0)
                ? {
                    key: "service_split",
                    node: (
                      <ServiceSplit
                        service_split={data.service_split}
                        compareHref={reportHref(agencyId, "compare_ranking", periodQuery)}
                      />
                    ),
                  }
                : null,
            ]
              .filter((section) => section !== null)
              .map((section, index) => (
                <RevealSection key={section.key} index={index}>
                  {section.node}
                </RevealSection>
              ))}
          </>
          );
          }}
        </AsyncSection>
      </div>

      <OverviewModal
        isOpen={open !== null}
        onClose={() => setOpen(null)}
        title={open === "concentration" ? t("overview.modal.concentration") : ""}
      >
        {data && open === "concentration" && (
          <ConcentrationBar
            concentration={data.concentration}
            periodRange={periodRange(data)}
            movers={data.movers}
            limit={20}
            variant="modal"
          />
        )}
      </OverviewModal>
    </ScopeHeaderProvider>
  );
}
