import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { GuestPrompt } from "./components/GuestPrompt";
import { Header } from "./components/Header";
import { Sidebar } from "./components/Sidebar";
import { TopProgressBar } from "./components/TopProgressBar";

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
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <GuestPrompt />
      <TopProgressBar />
      <Header />
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <Sidebar />
        <main style={{ flex: 1, overflowY: "auto", padding: 24 }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
