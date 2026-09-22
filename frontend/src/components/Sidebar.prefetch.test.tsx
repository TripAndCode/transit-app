import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";
import { prefetchRouteChunk } from "../routes/lazyTabs";

vi.mock("../routes/lazyTabs", () => ({ prefetchRouteChunk: vi.fn() }));

const prefetch = vi.mocked(prefetchRouteChunk);

function renderSidebar(path = "/agencies/1/overview") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/agencies/:agencyId/*" element={<Sidebar />} />
          </Routes>
        </MemoryRouter>
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

    await user.hover(screen.getByRole("link", { name: /Reports/ }));
    expect(prefetch).toHaveBeenCalledWith("reports");
  });

  it("warms the chunk on keyboard focus too, not just hover", async () => {
    renderSidebar();

    screen.getByRole("link", { name: /Segment analysis/ }).focus();
    expect(prefetch).toHaveBeenCalledWith("route-analysis");
  });

  it("warms the Ask chunk from its CTA", async () => {
    const user = userEvent.setup();
    renderSidebar();

    await user.hover(screen.getByRole("link", { name: /Ask/ }));
    expect(prefetch).toHaveBeenCalledWith("ask");
  });

  it("prefetches nothing until the user reaches for a destination", () => {
    renderSidebar();
    expect(prefetch).not.toHaveBeenCalled();
  });
});
