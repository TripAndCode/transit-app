import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { StopChart } from "../../components/analysis/StopChart";
import { DailyChart } from "../../components/charts/DailyChart";
import { StopEvidenceChart } from "../../tabs/ask/StopEvidenceChart";
import { useRevealOnScroll } from "./useRevealOnScroll";
import { PREVIEW_ASK_EVIDENCE, PREVIEW_DAILY_TREND, PREVIEW_ROUTE_STOPS, PREVIEW_ROUTE_STOPS_PREVIOUS } from "./previewData";
import "./ScrollNarrative.css";

function NarrativeSection({ titleKey, bodyKey, children }: { titleKey: string; bodyKey: string; children: ReactNode }) {
  const { t } = useTranslation();
  const [ref, revealed] = useRevealOnScroll<HTMLElement>();
  return (
    <section
      ref={ref}
      className={`landing-narrative-section landing-reveal landing-reveal--pending${revealed ? " landing-reveal--visible" : ""}`}
    >
      <div className="landing-narrative-section__text">
        <h2>{t(titleKey)}</h2>
        <p>{t(bodyKey)}</p>
      </div>
      <div className="landing-narrative-section__figure">{children}</div>
    </section>
  );
}

function RouteDelaySection() {
  const [selected, setSelected] = useState(PREVIEW_ROUTE_STOPS[0].stop_sequence);
  return (
    <NarrativeSection titleKey="landing.narrative.route.title" bodyKey="landing.narrative.route.body">
      <StopChart
        stops={PREVIEW_ROUTE_STOPS}
        previous={PREVIEW_ROUTE_STOPS_PREVIOUS}
        selected={selected}
        onSelect={setSelected}
      />
    </NarrativeSection>
  );
}

function DayTrendSection() {
  return (
    <NarrativeSection titleKey="landing.narrative.trend.title" bodyKey="landing.narrative.trend.body">
      {/* Illustrative, like the Ask embed: a drag here would rewrite the
          marketing page's from/to for a selection that changes nothing. */}
      <DailyChart days={PREVIEW_DAILY_TREND} brushable={false} />
    </NarrativeSection>
  );
}

function AskEvidenceSection() {
  return (
    <NarrativeSection titleKey="landing.narrative.ask.title" bodyKey="landing.narrative.ask.body">
      <StopEvidenceChart messageId={1} points={PREVIEW_ASK_EVIDENCE} />
    </NarrativeSection>
  );
}

/**
 * The landing page's post-hero scroll narrative: three sections, each
 * mounting a real, working chart component -- fed by static fixtures from
 * `previewData.ts`, never live data -- instead of the retired
 * `DashboardPreview` mocked-sidebar shell. `StopChart` (delay building along
 * a route), the Reports `DailyChart` (day-over-day comparison, drawn on via
 * `ChartEnter`'s `useDrawOn`), and Ask's `StopEvidenceChart` (the same
 * evidence view a real answer renders).
 */
export function ScrollNarrative() {
  return (
    <div className="landing-narrative">
      <RouteDelaySection />
      <DayTrendSection />
      <AskEvidenceSection />
    </div>
  );
}
