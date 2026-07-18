import { useEffect } from "react";
import { Outlet, useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDefaultRangeAnchor } from "./api/defaultRangeAnchor";
import { ActivityStrip } from "./components/ActivityStrip";
import { DataStalenessBanner } from "./components/DataStalenessBanner";
import { FeedHealthBanner } from "./components/FeedHealthBanner";
import { GuestPrompt } from "./components/GuestPrompt";
import { Sidebar } from "./components/Sidebar";

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
  useDefaultRangeAnchor(agencyId ? Number(agencyId) : null);
  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <Sidebar />
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflowY: "auto" }}>
        {/* Scoped to the content area, not the whole app shell — these are
            notices about the agency data being viewed, not app-wide chrome,
            so they shouldn't span above the sidebar (a full-height nav rail
            that has nothing to do with feed staleness or in-flight mutations). */}
        <GuestPrompt />
        <DataStalenessBanner />
        <FeedHealthBanner />
        <ActivityStrip />
        {/* flex: 1, not height: "100%" — main is now a flex column whose
            other children (the banners/strip above) take variable height, so
            a percentage here would overflow main's box; flex: 1 fills
            exactly what's left, same trick the outer app shell used before
            this block moved inside main. */}
        <div style={{ padding: 24, flex: 1, boxSizing: "border-box" }}>
          <Outlet key={agencyId ?? "root"} />
        </div>
      </main>
    </div>
  );
}
