import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RouteDossier } from "./RouteDossier";

vi.mock("../routes/lazyTabs", async () => {
  const { useScope } = await import("../api/scope");
  function RouteTab() {
    const [scope] = useScope();
    return <div>route-tab:{scope.routes.join(",")}</div>;
  }
  return { loadRouteAnalysisTab: () => Promise.resolve({ default: RouteTab }) };
});

function RoutesList() {
  const { search } = useLocation();
  return <div>routes-list{search}</div>;
}

function open(path: string) {
  const router = createMemoryRouter(
    [
      { path: "agencies/:agencyId/routes/:routeCode", element: <RouteDossier /> },
      { path: "agencies/:agencyId/routes", element: <RoutesList /> },
    ],
    { initialEntries: [path] },
  );
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("RouteDossier", () => {
  it("shows the route in its path and mirrors it into the scope", async () => {
    const router = open("/agencies/9/routes/50?from=2026-09-01");
    expect(await screen.findByText("route-tab:50")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes/50");
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get("routes")).toBe("50");
    expect(params.get("from")).toBe("2026-09-01");
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("follows a different single route picked inside it to that route's path", async () => {
    const router = open("/agencies/9/routes/50?routes=51&tab=stops");
    expect(await screen.findByText("route-tab:51")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes/51");
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get("routes")).toBe("51");
    expect(params.get("tab")).toBe("stops");
  });

  it("hands a several-route selection to the Routes list", async () => {
    const router = open("/agencies/9/routes/50?routes=51,52");
    expect(await screen.findByText(/^routes-list/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes");
    expect(new URLSearchParams(router.state.location.search).get("routes")).toBe("51,52");
  });

  it("decodes an encoded route code from its path", async () => {
    open("/agencies/9/routes/a%2Fb");
    expect(await screen.findByText("route-tab:a/b")).toBeInTheDocument();
  });
});
