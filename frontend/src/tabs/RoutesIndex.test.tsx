import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { RoutesIndex } from "./RoutesIndex";

vi.mock("../routes/lazyTabs", () => ({
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes, defaultReport }: { reportTypes: readonly string[]; defaultReport?: string }) => (
        <div>
          analysis-tab:{reportTypes.join(",")} default:{defaultReport ?? "none"}
        </div>
      ),
    }),
}));

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

  it("hosts the route ranking reports", async () => {
    open("/agencies/9/routes");
    expect(await screen.findByText("analysis-tab:ranking,ranking_best,on_time,worst_5min default:none")).toBeInTheDocument();
  });

  it("titles the screen with one level-1 heading", async () => {
    open("/agencies/9/routes");
    await screen.findByText(/^analysis-tab/);
    expect(screen.getByRole("heading", { level: 1, name: "Routes" })).toBeInTheDocument();
  });

  it("opens the report its sort names", async () => {
    open("/agencies/9/routes?sort=on_time");
    expect(await screen.findByText(/default:on_time/)).toBeInTheDocument();
  });

  it("leaves opening a route to the ranking's rows and the palette, with no separate picker", async () => {
    open("/agencies/9/routes");
    await screen.findByText(/^analysis-tab/);
    expect(screen.queryByRole("combobox", { name: "Open a route" })).not.toBeInTheDocument();
  });
});
