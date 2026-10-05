import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { Suspense, lazy } from "react";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Outlet, RouterProvider, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { decl, ruleBody } from "../test/cssRules";
import { NavPendingProvider, PendingNavLink } from "./navPending";
import { NavIndicator } from "./NavIndicator";

const NeverLoads = lazy(() => new Promise<never>(() => {}));

function rect(top: number, height: number, left = 0, width = 200): DOMRect {
  return { top, height, left, width, bottom: top + height, right: left + width, x: left, y: top, toJSON: () => ({}) } as DOMRect;
}

function Shell() {
  const { pathname } = useLocation();
  return (
    <NavPendingProvider>
      <nav aria-label="Destinations">
        <NavIndicator axis="y" watch={pathname} />
        <PendingNavLink to="/a">A</PendingNavLink>
        <PendingNavLink to="/b">B</PendingNavLink>
      </nav>
      <Suspense fallback={<p>fallback</p>}>
        <Outlet />
      </Suspense>
    </NavPendingProvider>
  );
}

function renderShell(at: string) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.tagName === "NAV") return rect(100, 80);
    if (this.textContent === "A") return rect(100, 40);
    if (this.textContent?.startsWith("B")) return rect(140, 40);
    return rect(0, 0);
  });
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Shell />,
        children: [
          { path: "a", element: <p>page a</p> },
          { path: "b", element: <NeverLoads /> },
          { path: "elsewhere", element: <p>elsewhere</p> },
        ],
      },
    ],
    { initialEntries: [at] },
  );
  renderWithProviders(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
  return document.querySelector<HTMLElement>(".nav-indicator")!;
}

describe("NavIndicator", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("sits on the current screen's link, hidden from assistive technology", () => {
    const indicator = renderShell("/a");
    expect(indicator).toHaveAttribute("aria-hidden", "true");
    expect(indicator.style.transform).toBe("translateY(0px)");
    expect(indicator.style.height).toBe("40px");
    expect(indicator.style.opacity).toBe("1");
  });

  it("travels to a clicked link at once, before its screen has arrived", async () => {
    const indicator = renderShell("/a");
    await userEvent.click(screen.getByRole("link", { name: /^B/ }));
    expect(screen.getByText("page a")).toBeInTheDocument();
    expect(indicator.style.transform).toBe("translateY(40px)");
    expect(indicator).toHaveAttribute("data-glide");
  });

  it("stays out of sight on a screen none of its links lead to", () => {
    const indicator = renderShell("/elsewhere");
    expect(indicator.style.opacity).toBe("0");
  });

  it("glides only where motion is welcome, and never into its first place", () => {
    const css = readFileSync(resolve(__dirname, "./NavIndicator.css"), "utf8");
    expect(decl(ruleBody(css, ".nav-indicator {"), "transition")).toBeNull();
    const allowed = ruleBody(css, "@media (prefers-reduced-motion: no-preference)");
    expect(decl(ruleBody(allowed, ".nav-indicator[data-glide]"), "transition")).toBe(
      "transform var(--dur-2) var(--ease-out), height var(--dur-2) var(--ease-out), width var(--dur-2) var(--ease-out)",
    );
  });
});
