import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RouteDossier } from "./RouteDossier";

const mounts = { count: 0 };

vi.mock("../routes/lazyTabs", async () => {
  const { useScope } = await import("../api/scope");
  function RouteTab() {
    const [scope, update] = useScope();
    useState(() => {
      mounts.count += 1;
      return null;
    });
    return (
      <div>
        <div>route-tab:{scope.routes.join(",")}</div>
        <button type="button" onClick={() => update({ routes: ["51"] })}>
          pick-51
        </button>
      </div>
    );
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
  it("scopes the screen to its path's route without writing a route filter into the URL", async () => {
    const router = open("/agencies/9/routes/50?from=2026-09-01");
    expect(await screen.findByText("route-tab:50")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes/50");
    expect(router.state.location.search).toBe("?from=2026-09-01");
  });

  it("moves to a different single route picked inside it, keeping its other params", async () => {
    const router = open("/agencies/9/routes/50?routes=51&tab=stops");
    expect(await screen.findByText("route-tab:51")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes/51");
    const params = new URLSearchParams(router.state.location.search);
    expect(params.has("routes")).toBe(false);
    expect(params.get("tab")).toBe("stops");
  });

  it("drops a route filter that only repeats its own route", async () => {
    const router = open("/agencies/9/routes/50?routes=50&from=2026-09-01");
    expect(await screen.findByText("route-tab:50")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/9/routes/50");
    expect(router.state.location.search).toBe("?from=2026-09-01");
  });

  it("keeps the same screen mounted when the route changes inside it", async () => {
    open("/agencies/9/routes/50");
    await screen.findByText("route-tab:50");
    const before = mounts.count;
    await userEvent.click(screen.getByRole("button", { name: "pick-51" }));
    expect(await screen.findByText("route-tab:51")).toBeInTheDocument();
    expect(mounts.count).toBe(before);
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
