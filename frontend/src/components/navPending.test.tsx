import { describe, it, expect } from "vitest";
import { Suspense, lazy } from "react";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { NavPendingProvider, PendingNavLink } from "./navPending";

// A chunk that never arrives: the navigation stays pending for the whole test.
const NeverLoads = lazy(() => new Promise<never>(() => {}));

function Shell() {
  return (
    <NavPendingProvider>
      <nav>
        <PendingNavLink to="/slow">Slow screen</PendingNavLink>
        <PendingNavLink to="/other">Other screen</PendingNavLink>
        <PendingNavLink to="/slow" spinner={false}>
          Tab
        </PendingNavLink>
      </nav>
      <Suspense fallback={<p>fallback</p>}>
        <Outlet />
      </Suspense>
    </NavPendingProvider>
  );
}

function renderShell() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Shell />,
        children: [
          { index: true, element: <p>old page</p> },
          { path: "slow", element: <NeverLoads /> },
          { path: "other", element: <p>other page</p> },
        ],
      },
    ],
    { initialEntries: ["/"] },
  );
  renderWithProviders(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
  return router;
}

describe("PendingNavLink", () => {
  it("keeps the current page and shows progress while the next screen loads", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("link", { name: /Slow screen/ }));
    expect(screen.getByText("old page")).toBeInTheDocument();
    expect(screen.queryByText("fallback")).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Slow screen/ })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("link", { name: /Other screen/ })).not.toHaveAttribute("aria-busy");
  });

  it("can leave the busy mark to the progress bar alone", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("link", { name: "Tab" }));
    const tab = screen.getByRole("link", { name: "Tab" });
    expect(tab).toHaveAttribute("aria-busy", "true");
    expect(tab.querySelector("svg")).toBeNull();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("tells a screen reader the next screen is loading, and when it is not", async () => {
    renderShell();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("");
    await userEvent.click(screen.getByRole("link", { name: /Slow screen/ }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading...");
  });

  it("shows no progress once a screen is in", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("link", { name: /Other screen/ }));
    await act(async () => {});
    expect(screen.getByText("other page")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("leaves a modified click to the browser", async () => {
    const router = renderShell();
    const user = userEvent.setup();
    await user.keyboard("{Meta>}");
    await user.click(screen.getByRole("link", { name: /Other screen/ }));
    await user.keyboard("{/Meta}");
    expect(router.state.location.pathname).toBe("/");
  });
});
