import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import App from "./App";
import { MapTab } from "./tabs/MapTab";
import { AskTab } from "./tabs/AskTab";
import { LiveTab } from "./tabs/LiveTab";
import { ReportsTab } from "./tabs/ReportsTab";
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
      { index: true, element: <Navigate to="map" replace /> },
      { path: "agencies/:agencyId", element: <Navigate to="map" replace /> },
      { path: "agencies/:agencyId/map", element: <MapTab /> },
      { path: "agencies/:agencyId/ask", element: <AskTab /> },
      { path: "agencies/:agencyId/live", element: <LiveTab /> },
      { path: "agencies/:agencyId/reports", element: <ReportsTab /> },
      { path: "agencies/:agencyId/reports/:reportType", element: <ReportsTab /> },
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
