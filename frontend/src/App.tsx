import { Suspense, useRef, ViewTransition } from "react";
import { Outlet, useMatch, useLocation, useMatches } from "react-router-dom";
import { AgencyDataEndProvider } from "./api/AgencyDataEndProvider";
import { useAnonymousFilterPersistence } from "./api/anonymousFilterPersistence";
import { useAgencyId } from "./api/useAgencyId";
import { ActivityStrip } from "./components/ActivityStrip";
import { CopilotPanel } from "./components/CopilotPanel";
import { DataStalenessBanner } from "./components/DataStalenessBanner";
import { FeedHealthBanner } from "./components/FeedHealthBanner";
import { HelpHint } from "./components/HelpHint";
import { FirstRunTourOnLive } from "./components/FirstRunTour";
import { ChunkLoading } from "./components/RoutePlaceholders";
import { Sidebar } from "./components/Sidebar";
import { NavPendingProvider } from "./components/navPending";
import "./styles/viewTransitions.css";
import { TopBar } from "./components/TopBar";
import { FOCUSED_TAB_PATTERN } from "./routes/focusedTabs";
import { CommandPalette } from "./components/CommandPalette";
import { useDocumentLocale } from "./i18n/useDocumentLocale";
import { useTranslation } from "react-i18next";
import { useAgencies } from "./api/hooks";
import { pageTitleKey } from "./routes/pageTitle";
import { routedPage, useRoutedPaneScroll } from "./hooks/useRoutedPaneScroll";

export default function App() {
  const { t } = useTranslation();
  // Remount the routed tab when the agency changes so no tab carries another
  // agency's in-component state across a switch (e.g. a selected Ask thread or
  // forecast route). Non-agency routes (account) share the "root" key.
  const agencyId = useMatch("/agencies/:agencyId/*")?.params.agencyId;
  const agencyIdNum = useAgencyId();
  const { pathname } = useLocation();
  const focused = FOCUSED_TAB_PATTERN.test(pathname);
  useAnonymousFilterPersistence(agencyIdNum);
  const titleKey = pageTitleKey(pathname);
  const agencyName = useAgencies().data?.find((a) => a.agency_id === agencyIdNum)?.agency_name;
  useDocumentLocale([titleKey ? t(titleKey) : null, agencyName]);
  const mainRef = useRef<HTMLElement>(null);
  useRoutedPaneScroll(mainRef, routedPage(useMatches()));
  return (
    <AgencyDataEndProvider>
      <NavPendingProvider>
        <div className="app-shell" style={{ display: "flex", height: "100dvh" }}>
          <CommandPalette />
          <Sidebar />
          <main ref={mainRef} className="app-main" style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflowY: "auto" }}>
            {agencyIdNum != null && <TopBar />}
            {/* Scoped to the content area, not the whole app shell — these are
                notices about the agency data being viewed, not app-wide chrome,
                so they shouldn't span above the sidebar (a full-height nav rail
                that has nothing to do with feed staleness or in-flight mutations). */}
            <div className="app-notice-stack">
              {!focused && <DataStalenessBanner />}
              {!focused && <FeedHealthBanner />}
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
                the incoming chunk resolves. A navigation started through
                navPending.tsx then commits as a view transition of this pane
                (styles/viewTransitions.css), without remounting it. */}
            <ViewTransition default={{ nav: "page-nav", default: "none" }}>
              <div style={{ display: "flex", flexDirection: "column", padding: "clamp(16px, 4vw, 24px)", flex: 1, minHeight: 0, boxSizing: "border-box" }}>
                <Suspense fallback={<ChunkLoading />}>
                  <Outlet key={agencyId ?? "root"} />
                </Suspense>
              </div>
            </ViewTransition>
          </main>
          {!focused && <CopilotPanel />}
          {/* Persisted in localStorage (transit.tourSeen); a no-op render
              once a visitor has finished or dismissed it. Leaving Live mid-tour
              closes it for that visit without marking it seen. */}
          <FirstRunTourOnLive />
        </div>
      </NavPendingProvider>
    </AgencyDataEndProvider>
  );
}
