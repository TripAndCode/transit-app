import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import {
  RedirectReportsToAnalysis,
  RedirectForecastToAnalysis,
  RedirectLiveToOperations,
  RedirectOverviewToOperations,
  RedirectMapToOperations,
} from "./routes/legacyRedirects";
import { RedirectNetworkToAgencyNetwork } from "./routes/networkRedirect";
import {
  loadAnalysisTab,
  loadAskTab,
  loadMapTab,
  loadNetworkTab,
  loadOverviewTab,
  loadReportsHomeTab,
  loadRouteAnalysisTab,
} from "./routes/lazyTabs";
import { i18nReady } from "./i18n";
import { refreshAuthStateOn401, retryUnlessAuthRequired } from "./api/authExpiry";
import App from "./App";
import { OnboardingGate } from "./components/OnboardingGate";
import { RequireAdmin } from "./components/RequireAdmin";
import { RequireAuth } from "./components/RequireAuth";
import { LocaleUnavailable } from "./components/LocaleUnavailable";
import { RouteError } from "./components/RouteError";
import { ToastProvider } from "./components/ui/Toast";
import { ChunkLoading } from "./components/RoutePlaceholders";
import "./styles/global.css";

// Tabs and pages are code-split per route — MapTab alone pulls in
// maplibre-gl (~800 KB), which nothing else needs. The tab loaders live in
// routes/lazyTabs.ts so the sidebar can prefetch through the very same
// import expressions; see that module for why sharing them matters.
// OnboardingGate is a deliberate exception, imported eagerly above: it sits
// on the "/" redirect-critical path (hit by every visitor) and has no heavy
// deps of its own, so lazy-splitting it would only add a chunk-fetch delay
// with no bundle-size benefit.
const OverviewTab = lazy(loadOverviewTab);
const MapTab = lazy(loadMapTab);
const AskTab = lazy(loadAskTab);
const AnalysisTab = lazy(loadAnalysisTab);
const RouteAnalysisTab = lazy(loadRouteAnalysisTab);
const ReportsHomeTab = lazy(loadReportsHomeTab);
const NetworkTab = lazy(loadNetworkTab);
const LandingPage = lazy(() => import("./pages/LandingPage").then((m) => ({ default: m.LandingPage })));
const LoginPage = lazy(() => import("./pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const AccountPage = lazy(() => import("./pages/AccountPage").then((m) => ({ default: m.AccountPage })));
const HelpPage = lazy(() => import("./pages/HelpPage").then((m) => ({ default: m.HelpPage })));
const LegalPage = lazy(() => import("./pages/LegalPage").then((m) => ({ default: m.LegalPage })));
const AdminUsersPage = lazy(() => import("./pages/admin/AdminUsersPage").then((m) => ({ default: m.AdminUsersPage })));
const AdminUserDetailPage = lazy(() =>
  import("./pages/admin/AdminUserDetailPage").then((m) => ({ default: m.AdminUserDetailPage })),
);
const AdminLayout = lazy(() =>
  import("./pages/admin/AdminLayout").then((m) => ({ default: m.AdminLayout }))
);
const AdminBoardPage = lazy(() =>
  import("./pages/admin/AdminBoardPage").then((m) => ({ default: m.AdminBoardPage }))
);
const AdminAgenciesPage = lazy(() =>
  import("./pages/admin/AdminAgenciesPage").then((m) => ({ default: m.AdminAgenciesPage }))
);
const AdminOpsPage = lazy(() =>
  import("./pages/admin/AdminOpsPage").then((m) => ({ default: m.AdminOpsPage }))
);
const AdminFlagsPage = lazy(() =>
  import("./pages/admin/AdminFlagsPage").then((m) => ({ default: m.AdminFlagsPage }))
);
const AdminArchitecturePage = lazy(() =>
  import("./pages/admin/AdminArchitecturePage").then((m) => ({ default: m.AdminArchitecturePage }))
);
const AdminAuditPage = lazy(() =>
  import("./pages/admin/AdminAuditPage").then((m) => ({ default: m.AdminAuditPage }))
);
const AdminAskOpsPage = lazy(() =>
  import("./pages/admin/AdminAskOpsPage").then((m) => ({ default: m.AdminAskOpsPage }))
);

/** Wrap a lazy route element in its own Suspense fallback. Only the
 *  routes that render outside <App /> need this — everything under "/"
 *  shares the one boundary App keeps above the Outlet, which is what lets
 *  a navigation's outgoing tab stay painted while the next chunk loads. */
function el(node: React.ReactNode) {
  return <Suspense fallback={<ChunkLoading />}>{node}</Suspense>;
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: (err) => onAuthError(err) }),
  mutationCache: new MutationCache({ onError: (err) => onAuthError(err) }),
  defaultOptions: {
    queries: { retry: retryUnlessAuthRequired, refetchOnWindowFocus: false },
  },
});
const onAuthError = refreshAuthStateOn401(queryClient);

const router = createBrowserRouter([
  // /welcome, /login and the two legal pages render outside <App /> so they
  // own the full viewport (no Header or sidebar), and outside RequireAuth so a
  // signed-out visitor can reach them.
  { path: "/welcome", element: el(<LandingPage />), errorElement: <RouteError /> },
  { path: "/login", element: el(<LoginPage />), errorElement: <RouteError /> },
  { path: "/privacy", element: el(<LegalPage doc="privacy" />), errorElement: <RouteError /> },
  { path: "/terms", element: el(<LegalPage doc="terms" />), errorElement: <RouteError /> },
  {
    path: "/",
    element: <RequireAuth><App /></RequireAuth>,
    // Catches render errors from any child route — a broken tab degrades to
    // an inline message instead of white-screening the whole app.
    errorElement: <RouteError />,
    children: [
      // Index has no static target — OnboardingGate owns the redirect once
      // agencies load. Sending Navigate to="operations" here loops with the
      // catch-all because /operations is not a registered route.
      { index: true, element: <OnboardingGate /> },
      { path: "agencies/:agencyId", element: <Navigate to="operations" replace /> },
      // The single canonical mount point for MapTab -- MapLibre owns
      // expensive GL context/tile state that must not be torn down and
      // rebuilt by navigating between sibling routes that both rendered it.
      { path: "agencies/:agencyId/operations", element: <MapTab /> },
      { path: "agencies/:agencyId/period-overview", element: <OverviewTab /> },
      // Pre-rename URLs redirect to the single mount point above rather than
      // rendering MapTab a second time.
      { path: "agencies/:agencyId/overview", element: <RedirectOverviewToOperations /> },
      { path: "agencies/:agencyId/map", element: <RedirectMapToOperations /> },
      { path: "agencies/:agencyId/ask", element: <AskTab /> },
      { path: "agencies/:agencyId/live", element: <RedirectLiveToOperations /> },
      { path: "agencies/:agencyId/analysis", element: <AnalysisTab /> },
      { path: "agencies/:agencyId/route-analysis", element: <RouteAnalysisTab /> },
      { path: "agencies/:agencyId/analysis/:reportType", element: <AnalysisTab /> },
      // Network was promoted from a standalone /network route into the
      // sidebar's uniform nav (artifact-parity Branch 2) — it needs an
      // agencyId in the URL now so the sidebar doesn't blank out when a
      // user lands here (Sidebar bails with no agencyId, matching every
      // other agency-scoped tab).
      { path: "agencies/:agencyId/network", element: <NetworkTab /> },
      { path: "agencies/:agencyId/reports", element: <ReportsHomeTab /> },
      // Old URLs from before Reports was renamed to Analysis and Forecast was
      // folded into it.
      { path: "agencies/:agencyId/reports/:reportType", element: <RedirectReportsToAnalysis /> },
      { path: "agencies/:agencyId/forecast", element: <RedirectForecastToAnalysis /> },
      // Legacy bare /network bookmark, from before the route above existed.
      { path: "network", element: <RedirectNetworkToAgencyNetwork /> },
      { path: "me", element: <AccountPage /> },
      { path: "help", element: <HelpPage /> },
      {
        path: "admin",
        element: <RequireAdmin><AdminLayout /></RequireAdmin>,
        children: [
          { index: true, element: <AdminBoardPage /> },
          { path: "agencies", element: <AdminAgenciesPage /> },
          {
            path: "users",
            element: <AdminUsersPage />,
            // Nested rather than a sibling route: AdminUsersPage renders the
            // list plus an <Outlet/>, so navigating to users/:uid overlays
            // the drawer on top of the still-mounted list instead of
            // replacing it with a standalone detail page.
            children: [{ path: ":uid", element: <AdminUserDetailPage /> }],
          },
          { path: "ops", element: <AdminOpsPage /> },
          { path: "ask", element: <AdminAskOpsPage /> },
          { path: "architecture", element: <AdminArchitecturePage /> },
          { path: "audit", element: <AdminAuditPage /> },
          { path: "flags", element: <AdminFlagsPage /> },
        ],
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

// Mounting waits for the active language's strings so the first paint is
// already translated. If they can't be fetched even after retries, a reload
// notice replaces an app that would show bare keys everywhere. Falling back
// to the other language is not attempted: whatever blocked one chunk (the
// network, a deploy that removed old chunks) blocks the other too, and
// switching would overwrite the visitor's stored language choice.
void i18nReady.then((stringsLoaded) => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      {stringsLoaded ? (
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <RouterProvider router={router} future={{ v7_startTransition: true }} />
          </ToastProvider>
        </QueryClientProvider>
      ) : (
        <LocaleUnavailable />
      )}
    </React.StrictMode>,
  );
});
