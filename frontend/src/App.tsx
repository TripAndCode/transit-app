import { Suspense, useEffect } from "react";
import { Outlet, useMatch, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAnonymousFilterPersistence } from "./api/anonymousFilterPersistence";
import { useDefaultRangeAnchor } from "./api/defaultRangeAnchor";
import { useAgencyId } from "./api/useAgencyId";
import { ActivityStrip } from "./components/ActivityStrip";
import { CopilotPanel } from "./components/CopilotPanel";
import { DataStalenessBanner } from "./components/DataStalenessBanner";
import { FeedHealthBanner } from "./components/FeedHealthBanner";
import { GuestPrompt } from "./components/GuestPrompt";
import { HelpHint } from "./components/HelpHint";
import { ChunkLoading } from "./components/RoutePlaceholders";
import { RouteTransition } from "./components/RouteTransition";
import { Sidebar } from "./components/Sidebar";

/** Tabs that own their whole viewport: the banners, HelpHint and CopilotPanel
 *  are hidden on these so nothing competes with the visualization.
 *
 *  `overview` and `map` stay listed even though both now render only a
 *  redirect. React-router's declarative `<Navigate>` fires from an effect
 *  after the redirect element renders once, so the pathname is briefly the
 *  pre-redirect one — listing them avoids a one-frame flash of the chrome.
 *
 *  Anything routed here is a place CopilotPanel can never appear, which is
 *  why `COPILOT_ROUTES` is asserted disjoint from this set. */
export const FOCUSED_TAB_SEGMENTS = [
  "operations",
  "overview",
  "map",
  "route-analysis",
  "reports",
  "ask",
] as const;

export const FOCUSED_TAB_PATTERN = new RegExp(`/agencies/[^/]+/(${FOCUSED_TAB_SEGMENTS.join("|")})$`);

/**
 * Keep <title> in sync with the active locale. The static `<title>` in
 * `index.html` is JP; this effect overwrites it post-mount and re-runs on
 * every language switch.
 */
function useDocumentTitle() {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    document.title = t("header.app_title");
  }, [t, i18n.language]);
}

export default function App() {
  useDocumentTitle();
  // Remount the routed tab when the agency changes so no tab carries another
  // agency's in-component state across a switch (e.g. a selected Ask thread or
  // forecast route). Non-agency routes (account) share the "root" key — Network
  // is now agency-scoped (agencies/:agencyId/network) and remounts like every
  // other tab.
  const agencyId = useMatch("/agencies/:agencyId/*")?.params.agencyId;
  const agencyIdNum = useAgencyId();
  const { pathname } = useLocation();
  const focused = FOCUSED_TAB_PATTERN.test(pathname);
  useDefaultRangeAnchor(agencyIdNum);
  useAnonymousFilterPersistence(agencyIdNum);
  return (
    <div className="app-shell" style={{ display: "flex", height: "100dvh" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflowY: "auto" }}>
        {/* Scoped to the content area, not the whole app shell — these are
            notices about the agency data being viewed, not app-wide chrome,
            so they shouldn't span above the sidebar (a full-height nav rail
            that has nothing to do with feed staleness or in-flight mutations).
            Data-quality warnings render before the guest-login nudge: both are
            persistent until dismissed, but a warning about the data itself
            should outrank a suggestion to sign in when more than one banner
            is showing at once. */}
        <div className="app-notice-stack">
          {!focused && <DataStalenessBanner />}
          {!focused && <FeedHealthBanner />}
          {!focused && <GuestPrompt />}
          <ActivityStrip />
        </div>
        {!focused && <HelpHint />}
        {/* flex: 1, not height: "100%" — main is now a flex column whose
            other children (the banners/strip above) take variable height, so
            a percentage here would overflow main's box; flex: 1 fills
            exactly what's left, same trick the outer app shell used before
            this block moved inside main. */}
        {/* One Suspense boundary for every routed tab, kept above the Outlet
            rather than wrapped around each route element. A per-route
            boundary is newly mounted on arrival and therefore always shows
            its fallback; this one already holds the outgoing tab, so the
            router's startTransition (main.tsx) can leave that painted until
            the incoming chunk resolves. RouteTransition then fades the new
            content in without remounting this wrapper. */}
        <RouteTransition style={{ display: "flex", flexDirection: "column", padding: 24, flex: 1, minHeight: 0, boxSizing: "border-box" }}>
          <Suspense fallback={<ChunkLoading />}>
            <Outlet key={agencyId ?? "root"} />
          </Suspense>
        </RouteTransition>
      </main>
      {!focused && <CopilotPanel />}
    </div>
  );
}
