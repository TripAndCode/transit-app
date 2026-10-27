import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { StopChart } from "../../components/analysis/StopChart";
import { DailyChart } from "../../components/charts/DailyChart";
import { useElementWidth } from "../../hooks/useElementWidth";
import { StopEvidenceChart } from "../../tabs/ask/StopEvidenceChart";
import { useRevealOnScroll } from "./useRevealOnScroll";
import { PREVIEW_DAILY_TREND, sampleAskEvidence, sampleRouteStops, type SampleStopKey } from "./previewData";
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
      <figure className="landing-narrative-section__figure">
        <figcaption className="landing-narrative-section__sample">
          {t("landing.narrative.sample_caption", { route: t("landing.hero_map.routes.bus12") })}
        </figcaption>
        {children}
      </figure>
    </section>
  );
}

/** A sample stop's name in the reader's language: the hero city's names. */
function useStopName(): (key: SampleStopKey) => string {
  const { t } = useTranslation();
  return (key) => t(`landing.hero_map.stops.${key}`);
}

function RouteDelaySection() {
  const { current, previous } = sampleRouteStops(useStopName());
  const [selected, setSelected] = useState(current[0].stop_sequence);
  // The unpadded box inside the figure: the chart draws at its width, so a
  // phone gets a narrower plot with fewer names instead of a sideways scroll.
  const [ref, width] = useElementWidth<HTMLDivElement>();
  return (
    <NarrativeSection titleKey="landing.narrative.route.title" bodyKey="landing.narrative.route.body">
      <div ref={ref}>
        <StopChart
          stops={current}
          previous={previous}
          selected={selected}
          onSelect={setSelected}
          width={width == null ? undefined : Math.floor(width)}
        />
      </div>
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
      <StopEvidenceChart messageId={1} points={sampleAskEvidence(useStopName())} />
    </NarrativeSection>
  );
}

/**
 * The landing page's post-hero scroll narrative: three sections, each
 * mounting a real, working chart component fed by one sample route from
 * `previewData.ts`, never live data. `StopChart` (delay building along the
 * route), the Reports `DailyChart` (day-over-day comparison, drawn on via
 * `ChartEnter`'s `useDrawOn`), and Ask's `StopEvidenceChart` (the same
 * evidence view a real answer renders). Each fits its figure at any width.
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
