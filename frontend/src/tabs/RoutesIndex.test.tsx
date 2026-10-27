import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { RoutesIndex } from "./RoutesIndex";

vi.mock("./RouteTableScreen", () => ({ RouteTableScreen: () => <div>route-table</div> }));

function open(path: string) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: [
      { route_id: "r50", route_short_name: "中央線", route_long_name: null, route_code: "50", trip_headsigns: [] },
      { route_id: "r51", route_short_name: "港線", route_long_name: null, route_code: "51", trip_headsigns: [] },
    ],
    isLoading: false,
  } as never);
  const router = createMemoryRouter(
    [
      { path: "agencies/:agencyId/routes", element: <RoutesIndex /> },
      { path: "agencies/:agencyId/routes/:routeCode", element: <div>dossier</div> },
    ],
    { initialEntries: [path] },
  );
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("RoutesIndex", () => {
  afterEach(() => vi.restoreAllMocks());

  it("hosts the one route table", async () => {
    open("/agencies/9/routes");
    expect(await screen.findByText("route-table")).toBeInTheDocument();
  });

  it("titles the screen with one level-1 heading", async () => {
    open("/agencies/9/routes");
    await screen.findByText("route-table");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Routes" })).toBeInTheDocument();
  });

  it("leaves opening a route to the table's rows and the palette, with no separate picker", async () => {
    open("/agencies/9/routes");
    await screen.findByText("route-table");
    expect(screen.queryByRole("combobox", { name: "Open a route" })).not.toBeInTheDocument();
  });
});
