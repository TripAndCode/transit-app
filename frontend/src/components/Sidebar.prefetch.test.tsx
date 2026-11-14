import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";
import { ToastProvider } from "./ui/Toast";
import { prefetchRouteChunk } from "../routes/lazyTabs";

vi.mock("../routes/lazyTabs", () => ({ prefetchRouteChunk: vi.fn() }));

const prefetch = vi.mocked(prefetchRouteChunk);

function mockMatchMedia(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(max-width: 640px)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

function renderSidebar(path = "/agencies/1/live") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ToastProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/agencies/:agencyId/*" element={<Sidebar />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("Sidebar chunk prefetch", () => {
  beforeEach(() => {
    prefetch.mockClear();
  });

  it("warms a tab's chunk when the pointer lands on its nav link", async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.hover(screen.getByRole("link", { name: "Routes" }));
    expect(prefetch).toHaveBeenCalledWith("routes");
  });

  it("warms the chunk on keyboard focus too, not just hover", async () => {
    renderSidebar();

    screen.getByRole("link", { name: "Reports" }).focus();
    expect(prefetch).toHaveBeenCalledWith("reports");
  });

  it("warms the Ask chunk from its rail entry", async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.hover(screen.getByRole("link", { name: "Ask" }));
    expect(prefetch).toHaveBeenCalledWith("ask");
  });

  it("prefetches nothing until the user reaches for a destination", () => {
    renderSidebar();
    expect(prefetch).not.toHaveBeenCalled();
  });

  describe("the phone More sheet's Bus Town link", () => {
    beforeEach(() => {
      mockMatchMedia(true);
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("warms the Bus Town chunk when the pointer lands on its link", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.hover(within(screen.getByRole("dialog")).getByRole("link", { name: "Bus Town" }));
      expect(prefetch).toHaveBeenCalledWith("bus-town");
    });

    it("warms it on keyboard focus too, not just hover", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));

      within(screen.getByRole("dialog")).getByRole("link", { name: "Bus Town" }).focus();
      expect(prefetch).toHaveBeenCalledWith("bus-town");
    });
  });
});
