import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import i18n from "../i18n";
import { RouteError } from "./RouteError";

function renderRouteError() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <div>ok</div>,
        errorElement: (
          <I18nextProvider i18n={i18n}>
            <RouteError />
          </I18nextProvider>
        ),
        loader: () => {
          throw new Error("boom");
        },
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("RouteError", () => {
  afterEach(() => vi.restoreAllMocks());

  it("logs the route error as a render-effect, not a synchronous render side effect", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderRouteError();
    await screen.findByRole("alert");
    // RouteError's own console.error call must have actually run (inside an
    // effect after commit) -- proving the log happens post-render rather
    // than being removed outright. Waited for rather than asserted outright:
    // the alert is in the DOM as soon as the commit lands, which is before
    // React has necessarily flushed the passive effect that does the logging.
    await waitFor(() => {
      expect(spy.mock.calls.some((call) => call[0] instanceof Error && call[0].message === "boom")).toBe(true);
    });
  });
});
