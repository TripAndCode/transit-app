import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Header } from "./Header";

function renderHeader(path: string) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/agencies/:agencyId/*" element={<Header />} />
            <Route path="/" element={<Header />} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("Header", () => {
  it("renders a Live link when viewing a specific agency", () => {
    renderHeader("/agencies/8/overview");
    expect(screen.getByRole("link", { name: "Latest observations" })).toBeTruthy();
  });

  it("points the Live link at the current agency's live route, preserving the filter query string", () => {
    renderHeader("/agencies/8/overview?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Latest observations" });
    expect(link).toHaveAttribute("href", "/agencies/8/live?from=2026-06-01&to=2026-06-07");
  });

  it("does not render a Live link outside any agency context", () => {
    renderHeader("/");
    expect(screen.queryByRole("link", { name: "Latest observations" })).toBeNull();
  });

  it("no longer renders a Network link (moved to the sidebar)", () => {
    renderHeader("/agencies/8/overview");
    expect(screen.queryByRole("link", { name: "Agencies" })).toBeNull();
  });
});
