import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import "./i18n";
import App from "./App";
import { OnboardingGate } from "./components/OnboardingGate";
import { RequireAdmin } from "./components/RequireAdmin";
import { RouteError } from "./components/RouteError";
import { ChunkLoading } from "./components/RoutePlaceholders";
import "./styles/global.css";
import "maplibre-gl/dist/maplibre-gl.css";

// Tabs and pages are code-split per route — MapTab alone pulls in
// maplibre-gl (~800 KB), which nothing else needs. Each loader maps the
// file's named export onto the default-export shape React.lazy expects.
// OnboardingGate is a deliberate exception, imported eagerly above: it sits
// on the "/" redirect-critical path (hit by every visitor) and has no heavy
// deps of its own, so lazy-splitting it would only add a chunk-fetch delay
// with no bundle-size benefit.
const OverviewTab = lazy(() => import("./tabs/OverviewTab").then((m) => ({ default: m.OverviewTab })));
const MapTab = lazy(() => import("./tabs/MapTab").then((m) => ({ default: m.MapTab })));
const AskTab = lazy(() => import("./tabs/AskTab").then((m) => ({ default: m.AskTab })));
const LiveTab = lazy(() => import("./tabs/LiveTab").then((m) => ({ default: m.LiveTab })));
const ReportsTab = lazy(() => import("./tabs/ReportsTab").then((m) => ({ default: m.ReportsTab })));
const NetworkTab = lazy(() => import("./tabs/NetworkTab").then((m) => ({ default: m.NetworkTab })));
const ForecastTab = lazy(() => import("./tabs/ForecastTab").then((m) => ({ default: m.ForecastTab })));
const LoginPage = lazy(() => import("./pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const AccountPage = lazy(() => import("./pages/AccountPage").then((m) => ({ default: m.AccountPage })));
const AdminUsersPage = lazy(() => import("./pages/AdminUsersPage").then((m) => ({ default: m.AdminUsersPage })));
const AdminUserDetailPage = lazy(() =>
  import("./pages/AdminUserDetailPage").then((m) => ({ default: m.AdminUserDetailPage })),
);
const AdminLayout = lazy(() =>
  import("./pages/admin/AdminLayout").then((m) => ({ default: m.AdminLayout }))
);
const AdminAgenciesPage = lazy(() =>
  import("./pages/admin/AdminAgenciesPage").then((m) => ({ default: m.AdminAgenciesPage }))
);
const AdminOpsPage = lazy(() =>
  import("./pages/admin/AdminOpsPage").then((m) => ({ default: m.AdminOpsPage }))
);

/** Wrap a lazy route element in the shared Suspense fallback. */
function el(node: React.ReactNode) {
  return <Suspense fallback={<ChunkLoading />}>{node}</Suspense>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  // /login renders outside <App /> so it owns the full viewport (no Header,
  // sidebar, or guest-prompt strip wrapping the centered auth card).
  { path: "/login", element: el(<LoginPage />), errorElement: <RouteError /> },
  {
    path: "/",
    element: <App />,
    // Catches render errors from any child route — a broken tab degrades to
    // an inline message instead of white-screening the whole app.
    errorElement: <RouteError />,
    children: [
      // Index has no static target — OnboardingGate owns the redirect once
      // agencies load. Sending Navigate to="overview" here loops with the
      // catch-all because /overview is not a registered route.
      { index: true, element: <OnboardingGate /> },
      { path: "agencies/:agencyId", element: <Navigate to="overview" replace /> },
      { path: "agencies/:agencyId/overview", element: el(<OverviewTab />) },
      { path: "agencies/:agencyId/map", element: el(<MapTab />) },
      { path: "agencies/:agencyId/ask", element: el(<AskTab />) },
      { path: "agencies/:agencyId/live", element: el(<LiveTab />) },
      { path: "agencies/:agencyId/reports", element: el(<ReportsTab />) },
      { path: "agencies/:agencyId/reports/:reportType", element: el(<ReportsTab />) },
      { path: "agencies/:agencyId/forecast", element: el(<ForecastTab />) },
      { path: "network", element: el(<NetworkTab />) },
      { path: "me", element: el(<AccountPage />) },
      {
        path: "admin",
        element: el(<RequireAdmin><AdminLayout /></RequireAdmin>),
        children: [
          { index: true, element: <Navigate to="agencies" replace /> },
          { path: "agencies", element: el(<AdminAgenciesPage />) },
          { path: "users", element: el(<AdminUsersPage />) },
          { path: "users/:uid", element: el(<AdminUserDetailPage />) },
          { path: "ops", element: el(<AdminOpsPage />) },
        ],
      },
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
