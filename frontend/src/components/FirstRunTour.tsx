import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { computeTooltipPosition, type TooltipPlacement } from "./tooltipPosition";
import { readTourSeen, writeTourSeen } from "../api/tourSeen";
import "./FirstRunTour.css";

type Step = { selector: string; placement: TooltipPlacement; titleKey: string; bodyKey: string };

const STEPS: readonly Step[] = [
  { selector: '[data-tour="filter-bar"]', placement: "bottom", titleKey: "tour.filters.title", bodyKey: "tour.filters.body" },
  { selector: '[data-tour="map-inspect"]', placement: "top", titleKey: "tour.map.title", bodyKey: "tour.map.body" },
  { selector: '[data-tour="ask-nav"]', placement: "right", titleKey: "tour.ask.title", bodyKey: "tour.ask.body" },
];

/** How often to re-check for the current step's target element while it
 *  hasn't appeared yet -- the dashboard's own data fetches finish
 *  asynchronously, so a step's anchor (the filter dock, the observed-trips
 *  panel) isn't guaranteed to exist the instant this component mounts or a
 *  step advances. */
const FIND_RETRY_MS = 250;

/**
 * Three-step first-run coach mark over the real, signed-in dashboard
 * (filter bar -> map/inspect -> Ask), anchored with the same
 * viewport-aware positioning `Tooltip` uses (`computeTooltipPosition`).
 * Persistence mirrors `welcomeSeen.ts`: "seen" is written only when a
 * visitor finishes the last step or explicitly dismisses the tour; "later"
 * closes it for this mount without writing, so it resumes on the next one.
 *
 * Position is written straight to the panel DOM node rather than held in
 * React state (the same trick `Tooltip.tsx` uses): a poll that hasn't found
 * anything new yet never re-renders this component.
 */
export function FirstRunTour() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(() => readTourSeen() === "seen");
  const [stepIndex, setStepIndex] = useState(0);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const step = STEPS[stepIndex];

  useLayoutEffect(() => {
    if (dismissed) return;
    const panel = panelRef.current;
    if (!panel) return;
    function place() {
      const target = document.querySelector(step.selector);
      if (!target || !panel) {
        if (panel) panel.hidden = true;
        return;
      }
      const pos = computeTooltipPosition(
        target.getBoundingClientRect(),
        panel.getBoundingClientRect(),
        step.placement,
        { width: window.innerWidth, height: window.innerHeight },
      );
      panel.style.left = `${pos.left}px`;
      panel.style.top = `${pos.top}px`;
      panel.dataset.placement = pos.placement;
      panel.hidden = false;
    }
    place();
    const intervalId = window.setInterval(place, FIND_RETRY_MS);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [dismissed, step.selector, step.placement]);

  if (dismissed) return null;

  function finish() {
    writeTourSeen();
    setDismissed(true);
  }
  function later() {
    setDismissed(true);
  }
  function next() {
    if (stepIndex === STEPS.length - 1) {
      finish();
      return;
    }
    setStepIndex((i) => i + 1);
  }

  const isLast = stepIndex === STEPS.length - 1;

  return createPortal(
    <div ref={panelRef} className="first-run-tour" role="dialog" aria-label={t(step.titleKey)} hidden>
      <button type="button" className="first-run-tour__close" aria-label={t("tour.dismiss")} onClick={finish}>
        ×
      </button>
      <p className="first-run-tour__step">{t("tour.step_of", { step: stepIndex + 1, total: STEPS.length })}</p>
      <h4 className="first-run-tour__title">{t(step.titleKey)}</h4>
      <p className="first-run-tour__body">{t(step.bodyKey)}</p>
      <div className="first-run-tour__actions">
        <button type="button" className="first-run-tour__later" onClick={later}>
          {t("tour.later")}
        </button>
        <button type="button" className="first-run-tour__next" onClick={next}>
          {isLast ? t("tour.done") : t("tour.next")}
        </button>
      </div>
    </div>,
    document.body,
  );
}
