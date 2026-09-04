import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { PreviewSidebar, type PreviewTabKey } from "./PreviewSidebar";
import { PreviewOverviewPanel } from "./PreviewOverviewPanel";
import { PreviewMapPanel } from "./PreviewMapPanel";
import { PreviewAnalysisPanel } from "./PreviewAnalysisPanel";
import { PreviewNetworkPanel } from "./PreviewNetworkPanel";
import { PreviewLivePanel } from "./PreviewLivePanel";
import { PreviewAskPanel } from "./PreviewAskPanel";
import { PreviewHelpHint } from "./PreviewHelpHint";
import type { PreviewAgencyKey } from "./previewData";
import { useMediaQuery } from "../../hooks/useMediaQuery";

// The tabs the living-demo timer cycles through, in the same order as the
// real sidebar's nav list (`PreviewSidebar`'s `ITEMS`). "ask" is deliberately
// excluded -- it's a CTA the visitor opts into, not a peer tab, matching how
// `PreviewSidebar` itself treats it.
const AUTO_ADVANCE_ORDER: PreviewTabKey[] = ["overview", "map", "analysis", "network", "live"];

// A visitor who never touches the preview should still see the whole cycle
// play out like a demo video would; one tab change every few seconds reads
// as a guided tour rather than a flicker.
const AUTO_ADVANCE_INTERVAL_MS = 4500;

// Once a visitor actually clicks or keys into the preview, give them this
// long to keep exploring before the auto-advance resumes narrating on its
// own -- long enough not to yank the tab away mid-read, short enough that
// the demo doesn't look stuck once they've moved on.
const AUTO_ADVANCE_RESUME_DELAY_MS = 6000;

function nextAutoAdvanceTab(current: PreviewTabKey): PreviewTabKey {
  const idx = AUTO_ADVANCE_ORDER.indexOf(current);
  // "ask" (or any future tab outside the cycle) isn't in AUTO_ADVANCE_ORDER
  // -- restart the cycle from the top instead of relying on -1 + 1 === 0.
  if (idx === -1) return AUTO_ADVANCE_ORDER[0];
  return AUTO_ADVANCE_ORDER[(idx + 1) % AUTO_ADVANCE_ORDER.length];
}

/** The landing page's post-hero section: a dashboard-preview shell that
 *  structurally matches the real signed-in app -- a flex row of the
 *  persistent, collapsible `Sidebar.tsx` and a main content column
 *  (`App.tsx`) -- instead of item 63's flat, single-widget tab explorer.
 *  Everything below the hero lives inside one bounded "preview viewport"
 *  box (not the full 100vh the real app gets, since this section still
 *  sits on an otherwise-scrolling marketing page below the hero), but the
 *  layout, nav, and every control inside it are the real structure and real
 *  interaction, not decorative chrome.
 *
 *  By default the preview auto-advances through its tabs on a timer, like a
 *  recorded demo would, so a visitor who never clicks anything still sees
 *  every screen. The timer lives entirely in this effect's own
 *  `setInterval`, gated by refs (`hoveredRef`/`lastInteractionAtRef`) rather
 *  than state, so hovering or clicking doesn't need to re-render the effect
 *  itself -- only `setActiveTab` (called from inside the interval callback,
 *  not during render) drives a re-render. Skipped entirely for
 *  `prefers-reduced-motion`. */
export function DashboardPreview() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<PreviewTabKey>("overview");
  const [agencyKey, setAgencyKey] = useState<PreviewAgencyKey>("riverside");
  const prefersReducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const hoveredRef = useRef(false);
  const lastInteractionAtRef = useRef(0);

  useEffect(() => {
    if (prefersReducedMotion) return;
    const intervalId = window.setInterval(() => {
      // Skip work while the tab is backgrounded, mirroring
      // useCityMapAnimation's own visibility guard.
      if (document.hidden) return;
      if (hoveredRef.current) return;
      if (Date.now() - lastInteractionAtRef.current < AUTO_ADVANCE_RESUME_DELAY_MS) return;
      setActiveTab((current) => nextAutoAdvanceTab(current));
    }, AUTO_ADVANCE_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [prefersReducedMotion]);

  function markInteraction() {
    lastInteractionAtRef.current = Date.now();
  }

  return (
    <section aria-labelledby="dashboard-preview-heading" style={{ maxWidth: 1040, margin: "0 auto", padding: "40px 24px 64px" }}>
      <h2
        id="dashboard-preview-heading"
        style={{ margin: "0 0 16px", fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}
      >
        {t("landing.preview.heading")}
      </h2>
      <div
        onMouseEnter={() => {
          hoveredRef.current = true;
        }}
        onMouseLeave={() => {
          hoveredRef.current = false;
        }}
        onClickCapture={markInteraction}
        onKeyDownCapture={markInteraction}
        style={{
          display: "flex",
          height: 560,
          background: "var(--bg-page)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          overflow: "hidden",
          boxShadow: "0 8px 32px rgba(0,0,0,0.12)",
        }}
      >
        <PreviewSidebar
          activeTab={activeTab}
          onSelectTab={setActiveTab}
          agencyKey={agencyKey}
          onSelectAgency={setAgencyKey}
        />
        <main style={{ flex: 1, position: "relative", overflow: "hidden" }}>
          <div style={{ position: "absolute", inset: 0, overflowY: activeTab === "map" ? "hidden" : "auto" }}>
            {activeTab === "overview" && <PreviewOverviewPanel agencyKey={agencyKey} />}
            {activeTab === "map" && <PreviewMapPanel />}
            {activeTab === "analysis" && <PreviewAnalysisPanel />}
            {activeTab === "network" && <PreviewNetworkPanel selectedKey={agencyKey} onSelect={setAgencyKey} />}
            {activeTab === "live" && <PreviewLivePanel />}
            {activeTab === "ask" && <PreviewAskPanel />}
          </div>
          <PreviewHelpHint />
        </main>
      </div>
    </section>
  );
}
