import { describe, it, expect, vi, beforeEach } from "vitest";
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
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, text: async () => "# Manual\n\n## 3. Overview tab\n\nExcerpt.\n" })),
    );
  });

  it("renders the hero headline, subtitle, and a sign-in CTA linking to /login", () => {
    renderLanding();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "What's happening right now — and where it's happening.",
    );
    expect(
      screen.getByText(
        "Live vehicle positions, delay analysis, and network comparisons for every agency you track.",
      ),
    ).toBeTruthy();
    const cta = screen.getByRole("link", { name: "Sign in" });
    expect(cta).toHaveAttribute("href", "/login");
  });

  it("renders the tab explorer below the hero", () => {
    renderLanding();
    expect(screen.getByRole("heading", { name: "See what's inside" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Overview/ })).toBeTruthy();
  });
});
