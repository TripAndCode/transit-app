import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { SidebarUserMenu } from "./SidebarUserMenu";

function renderMenu(onOpenSettings = vi.fn()) {
  // retry: false — without a real backend, /api/me and /api/config fail
  // immediately in this test environment; default retries would otherwise
  // keep isLoading true past findByRole's timeout.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <SidebarUserMenu onOpenSettings={onOpenSettings} />
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>
  );
  return { onOpenSettings };
}

describe("SidebarUserMenu", () => {
  beforeEach(() => localStorage.clear());
  afterEach(async () => {
    delete document.documentElement.dataset.theme;
    // The i18n instance is a shared singleton across tests in this file —
    // reset it to the suite's baseline (jsdom's navigator language) so a
    // language switch in one test doesn't leak into the next.
    await i18n.changeLanguage("en");
  });

  it("renders the guest label and avatar initial once the session/config queries settle", async () => {
    renderMenu();
    expect(await screen.findByRole("button", { name: "Account menu" })).toBeTruthy();
    expect(screen.getByText("Guest")).toBeTruthy();
    expect(screen.getByText("G")).toBeTruthy();
  });

  it("opens the popover menu on click and closes it on a second click", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeTruthy();
    await user.click(trigger);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("switches the language when the language menu item is clicked", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    // Baseline is English; the row shows the CURRENT locale ("English") and
    // clicking it switches to the other supported one ("ja").
    await user.click(screen.getByRole("menuitem", { name: /English/ }));
    await waitFor(() => expect(i18n.resolvedLanguage).toBe("ja"));
  });

  it("toggles data-theme when the theme menu item is clicked", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    const themeItem = screen.getByRole("menuitem", { name: /switch to (light|dark) mode/i });
    await user.click(themeItem);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBeTruthy());
  });

  it("calls onOpenSettings and closes the popover when the settings menu item is clicked", async () => {
    const user = userEvent.setup();
    const { onOpenSettings } = renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("does not render a sign-in row when the backend reports auth disabled (the default in tests, no server)", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    expect(screen.queryByRole("menuitem", { name: "Sign in" })).toBeNull();
  });
});
