import { useEffect } from "react";
import { Outlet, useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ActivityStrip } from "./components/ActivityStrip";
import { DataStalenessBanner } from "./components/DataStalenessBanner";
import { FeedHealthBanner } from "./components/FeedHealthBanner";
import { GuestPrompt } from "./components/GuestPrompt";
import { Header } from "./components/Header";
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
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <GuestPrompt />
      <Header />
      <DataStalenessBanner />
      <FeedHealthBanner />
      <ActivityStrip />
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <Sidebar />
        <main style={{ flex: 1, overflowY: "auto" }}>
          <div style={{ padding: 24 }}>
            <Outlet key={agencyId ?? "root"} />
          </div>
        </main>
      </div>
    </div>
  );
}
