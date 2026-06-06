import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import "./i18n";
import App from "./App";
import { OverviewTab } from "./tabs/OverviewTab";
import { MapTab } from "./tabs/MapTab";
import { AskTab } from "./tabs/AskTab";
import { LiveTab } from "./tabs/LiveTab";
import { ReportsTab } from "./tabs/ReportsTab";
import { LoginPage } from "./pages/LoginPage";
import { AccountPage } from "./pages/AccountPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AdminUserDetailPage } from "./pages/AdminUserDetailPage";
import { RequireAdmin } from "./components/RequireAdmin";
import "./styles/global.css";
import "maplibre-gl/dist/maplibre-gl.css";

function IndexLoadingPlaceholder() {
  const { t } = useTranslation();
  return <div style={{ padding: 24, color: "var(--text-tertiary)" }}>{t("common.loading_agencies")}</div>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  // /login renders outside <App /> so it owns the full viewport (no Header,
  // sidebar, or guest-prompt strip wrapping the centered auth card).
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <App />,
    children: [
      // Index has no static target — AgencyPicker (in Header) auto-redirects
      // to /agencies/<first>/map once agencies load. Sending Navigate to="overview"
      // here loops with the catch-all because /overview is not a registered route.
      { index: true, element: <IndexLoadingPlaceholder /> },
      { path: "agencies/:agencyId", element: <Navigate to="overview" replace /> },
      { path: "agencies/:agencyId/overview", element: <OverviewTab /> },
      { path: "agencies/:agencyId/map", element: <MapTab /> },
      { path: "agencies/:agencyId/ask", element: <AskTab /> },
      { path: "agencies/:agencyId/live", element: <LiveTab /> },
      { path: "agencies/:agencyId/reports", element: <ReportsTab /> },
      { path: "agencies/:agencyId/reports/:reportType", element: <ReportsTab /> },
      { path: "me", element: <AccountPage /> },
      { path: "admin/users", element: <RequireAdmin><AdminUsersPage /></RequireAdmin> },
      { path: "admin/users/:uid", element: <RequireAdmin><AdminUserDetailPage /></RequireAdmin> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
