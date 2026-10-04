import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import ja from "../i18n/locales/ja.json";
import { ToastProvider } from "./ui/Toast";
import { SidebarUserMenu } from "./SidebarUserMenu";
import * as auth from "../api/auth";
import * as config from "../api/config";

function renderMenu(onOpenSettings = vi.fn()) {
  // retry: false — without a real backend, /api/me and /api/config fail
  // immediately in this test environment; default retries would otherwise
  // keep isLoading true past findByRole's timeout.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ToastProvider>
          <MemoryRouter>
            <SidebarUserMenu onOpenSettings={onOpenSettings} />
          </MemoryRouter>
        </ToastProvider>
      </I18nextProvider>
    </QueryClientProvider>
  );
  return { onOpenSettings };
}

/** Unloads Japanese, as it is before a visitor first switches to it, and
 *  holds its fetch open until `finish` runs. The fetch then either delivers
 *  the strings or fails, leaving Japanese unloaded. Call it before rendering:
 *  the i18n object a component gets from `useTranslation` copies the
 *  instance's methods when it is created, so a later spy is never reached. */
function holdJapaneseFetch({ succeeds }: { succeeds: boolean }) {
  i18n.removeResourceBundle("ja", "translation");
  let finish!: () => void;
  const fetched = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const reload = vi.spyOn(i18n, "reloadResources").mockImplementation(async () => {
    await fetched;
    if (succeeds) i18n.addResourceBundle("ja", "translation", ja);
  });
  return { reload, finish: () => act(async () => finish()) };
}

async function openLanguageToggle() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Account menu" }));
  return { user, toggle: screen.getByRole("menuitemradio", { name: /日本語/ }) };
}

describe("SidebarUserMenu", () => {
  beforeEach(() => localStorage.clear());
  afterEach(async () => {
    delete document.documentElement.dataset.theme;
    // The i18n instance is a shared singleton across tests in this file —
    // restore the Japanese strings a test may have unloaded and reset the
    // language to the suite's baseline (jsdom's navigator language) so a
    // language switch in one test doesn't leak into the next.
    vi.restoreAllMocks();
    if (!i18n.hasResourceBundle("ja", "translation")) i18n.addResourceBundle("ja", "translation", ja);
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
    await user.click(screen.getByRole("menuitemradio", { name: /日本語/ }));
    await waitFor(() => expect(i18n.resolvedLanguage).toBe("ja"));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("marks the language toggle busy while the other language loads, and frees it once switched", async () => {
    const { finish } = holdJapaneseFetch({ succeeds: true });
    renderMenu();
    const { user, toggle } = await openLanguageToggle();
    expect(toggle).toHaveAttribute("aria-busy", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-busy", "true");
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(i18n.resolvedLanguage).toBe("en");

    await finish();
    expect(i18n.resolvedLanguage).toBe("ja");
    expect(toggle).toHaveAttribute("aria-busy", "false");
    expect(toggle).toHaveAttribute("aria-disabled", "false");
  });

  it("does not start a second switch when the toggle is clicked again while one is loading", async () => {
    const { reload, finish } = holdJapaneseFetch({ succeeds: true });
    renderMenu();
    const { user, toggle } = await openLanguageToggle();

    await user.click(toggle);
    await user.click(toggle);
    expect(reload).toHaveBeenCalledOnce();

    await finish();
    expect(i18n.resolvedLanguage).toBe("ja");
    expect(reload).toHaveBeenCalledOnce();
  });

  it("keeps the language and says the switch failed when the other language can't be loaded", async () => {
    const { finish } = holdJapaneseFetch({ succeeds: false });
    renderMenu();
    const { user, toggle } = await openLanguageToggle();

    await user.click(toggle);
    expect(screen.queryByRole("status")).toBeNull();
    await finish();

    expect(i18n.resolvedLanguage).toBe("en");
    expect(toggle).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Couldn't switch the language. Please check your connection and try again.",
    );
  });

  it("offers system, light and dark as a checked radio set, with system checked by default", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    const options = within(screen.getByRole("group", { name: "Appearance" })).getAllByRole("menuitemradio");
    expect(options.map((o) => o.textContent)).toEqual(["System", "Light", "Dark"]);
    expect(options.map((o) => o.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);
  });

  it("applies and persists the theme chosen from the radio set", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = await screen.findByRole("button", { name: "Account menu" });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitemradio", { name: "Dark" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem("transit.theme")).toBe("dark");
    expect(screen.getByRole("menuitemradio", { name: "Dark" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("menuitemradio", { name: "System" }).getAttribute("aria-checked")).toBe("false");
  });

  it("sets each of the three theme values from the radio set", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    for (const [label, value] of [
      ["Light", "light"],
      ["Dark", "dark"],
      ["System", "system"],
    ] as const) {
      await user.click(screen.getByRole("menuitemradio", { name: label }));
      expect(localStorage.getItem("transit.theme")).toBe(value);
      const checked = within(screen.getByRole("group", { name: "Appearance" }))
        .getAllByRole("menuitemradio")
        .filter((o) => o.getAttribute("aria-checked") === "true");
      expect(checked.map((o) => o.textContent)).toEqual([label]);
    }
  });

  it("offers the theme only through the radio set, with no separate two-way toggle", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.queryByRole("menuitem", { name: /dark|light|theme/i })).toBeNull();
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

  it("shows both languages under a visible label, the current one checked", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    const languages = screen.getByRole("group", { name: "Language" });
    const options = within(languages).getAllByRole("menuitemradio");
    expect(options.map((o) => o.textContent)).toEqual(["日本語", "English"]);
    expect(options.map((o) => o.getAttribute("aria-checked"))).toEqual(["false", "true"]);
    expect(screen.getByText("Language")).toBeVisible();
  });

  it("heads the theme choices with a visible Appearance label", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.getByText("Appearance")).toBeVisible();
  });

  it("signs out from the menu when signed in", async () => {
    const mutate = vi.fn();
    vi.spyOn(config, "useConfig").mockReturnValue({ data: { auth_enabled: true }, isLoading: false } as never);
    vi.spyOn(auth, "useSession").mockReturnValue({
      data: { user_id: 1, email: "yo@example.com", name: "Yo", avatar_url: null, role: "user", identities: [] },
      isLoading: false,
    } as never);
    vi.spyOn(auth, "useLogout").mockReturnValue({ mutate, isPending: false } as never);
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(mutate).toHaveBeenCalledOnce();
    // The spies above call no hooks; unmount before afterEach restores the
    // real ones, or a re-render would run a different hook sequence.
    cleanup();
  });

  it("offers no sign-out to a guest", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).toBeNull();
  });
});
