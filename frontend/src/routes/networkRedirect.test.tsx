import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { RedirectNetworkToAgencyNetwork } from "./networkRedirect";

function DummyTarget({ label }: { label: string }) {
  return <div>{label}</div>;
}

describe("RedirectNetworkToAgencyNetwork", () => {
  beforeEach(() => localStorage.clear());

  it("redirects to the last-selected agency's network view, preserving the query string", () => {
    localStorage.setItem("transit.lastAgency", "8");
    const router = createMemoryRouter(
      [
        { path: "network", element: <RedirectNetworkToAgencyNetwork /> },
        { path: "agencies/:agencyId/network", element: <DummyTarget label="agency-network" /> },
      ],
      { initialEntries: ["/network?from=2026-06-07&to=2026-06-10"] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByText("agency-network")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/agencies/8/network");
    expect(router.state.location.search).toBe("?from=2026-06-07&to=2026-06-10");
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("redirects to / when no agency was ever selected", () => {
    const router = createMemoryRouter(
      [
        { path: "network", element: <RedirectNetworkToAgencyNetwork /> },
        { path: "/", element: <DummyTarget label="root" /> },
      ],
      { initialEntries: ["/network"] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByText("root")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });
});
