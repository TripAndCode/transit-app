import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { LandingPage } from "./LandingPage";

void i18n.changeLanguage("en");

function renderLanding() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={["/welcome"]}>
          <LandingPage />
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LandingPage", () => {
  it("renders the hero headline, subtitle, and a sign-in CTA linking to /login", () => {
    renderLanding();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "What's happening right now — and where it's happening.",
    );
    expect(
      screen.getByText(
        "Latest reported stops, current delays, historical analysis, and network comparisons for every agency you track.",
      ),
    ).toBeTruthy();
    const cta = screen.getByRole("link", { name: "Sign in" });
    expect(cta).toHaveAttribute("href", "/login");
  });

  it("renders a secondary guest link to the real guest-accessible dashboard", () => {
    renderLanding();
    const guestLink = screen.getByRole("link", { name: "Continue as a guest" });
    expect(guestLink).toHaveAttribute("href", "/");
    // Still exactly one primary sign-in CTA -- the guest link is additive,
    // not a replacement or a competing same-weight button.
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("renders the scroll narrative's real chart sections below the hero, not the retired DashboardPreview mock", () => {
    renderLanding();
    // The three narrative section headings.
    expect(screen.getByRole("heading", { name: "Delay builds along a route" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Every day, compared" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Ask, get evidence" })).toBeTruthy();
    // Real components, not a mocked shell -- see ScrollNarrative.test.tsx for
    // the full per-section assertions.
    expect(screen.getByRole("group", { name: "Delay by stop" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Daily average delay chart" })).toBeTruthy();
  });
});
