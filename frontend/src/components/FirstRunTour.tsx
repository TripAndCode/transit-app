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
  // "unavailable" as well as "seen": a store that cannot be read or written
  // can never remember a dismissal, so running the tour on every single
  // mount is worse than not running it. This is the distinction the
  // tri-state exists for.
  const [dismissed, setDismissed] = useState(() => readTourSeen() !== "unseen");
  const [stepIndex, setStepIndex] = useState(0);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const step = STEPS[stepIndex];

  useLayoutEffect(() => {
    if (dismissed) return;
    const panel = panelRef.current;
    if (!panel) return;
    // Hidden until `place` has found an anchor. Set here rather than as a
    // JSX attribute: `place` writes straight to the node, so React
    // re-asserting the attribute on an unrelated re-render would hide a
    // panel that is already placed, until the next tick moved it back.
    panel.hidden = true;
    // Focus moves on the transition into view, not at mount: an anchor the
    // dashboard has not finished fetching yet is the case the retry poll
    // exists for, and a panel that appears three ticks later still has to
    // announce itself. Once per step -- a reposition is not a new arrival.
    let announced = false;
    function place() {
      // A poll that forces layout on a tab nobody is looking at buys
      // nothing; the anchor cannot have moved under the visitor.
      if (document.hidden) return;
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
      if (!announced) {
        announced = true;
        // Captured before the panel takes focus, so dismissing returns the
        // visitor to what they were on rather than the tour's own button.
        previouslyFocused.current ??= document.activeElement as HTMLElement | null;
        panel.querySelector<HTMLElement>("button")?.focus();
      }
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

  /** Hand focus back to wherever it was before the tour took it. */
  function restoreFocus() {
    previouslyFocused.current?.focus();
    previouslyFocused.current = null;
  }
  function finish() {
    writeTourSeen();
    setDismissed(true);
    restoreFocus();
  }
  function later() {
    setDismissed(true);
    restoreFocus();
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
    <div ref={panelRef} className="first-run-tour" role="dialog" aria-label={t(step.titleKey)}>
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
