import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import App from "./App";
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

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      // Index has no static target — AgencyPicker (in Header) auto-redirects
      // to /agencies/<first>/map once agencies load. Sending Navigate to="map"
      // here loops with the catch-all because /map is not a registered route.
      { index: true, element: <div style={{ padding: 24, color: "var(--text-tertiary)" }}>事業者を読み込み中...</div> },
      { path: "agencies/:agencyId", element: <Navigate to="map" replace /> },
      { path: "agencies/:agencyId/map", element: <MapTab /> },
      { path: "agencies/:agencyId/ask", element: <AskTab /> },
      { path: "agencies/:agencyId/live", element: <LiveTab /> },
      { path: "agencies/:agencyId/reports", element: <ReportsTab /> },
      { path: "agencies/:agencyId/reports/:reportType", element: <ReportsTab /> },
      { path: "login", element: <LoginPage /> },
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
