import { describe, it, expect, vi, afterEach } from "vitest";
import { SCOPE_EXTRAS_NONE, type Scope } from "../api/scope";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as client from "../api/client";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { PresetMenu } from "./PresetMenu";
import * as auth from "../api/auth";

const SCOPE: Scope = { ...SCOPE_EXTRAS_NONE, from: "2026-01-01", to: "2026-01-30", dow: "all", time_band: "all", service: "all", routes: [] };
const SAVED: Scope = { ...SCOPE, dow: "weekday" };
/** What a reset gives back, so SCOPE changes nothing. */
const DEFAULT_PERIOD = { from: "2026-01-01", to: "2026-01-30" };
const PHRASES = { t: i18n.getFixedT("en"), agencyName: "Aomori", routeLabel: (c: string) => `R${c}` };

type PresetRow = { preset_id: number; agency_id: number; name: string; range_ctx: unknown };

function renderMenu(current: Scope = SCOPE) {
  const onSelect = vi.fn();
  renderWithProviders(
    <PresetMenu
      agencyId={1}
      currentRangeCtx={current}
      phrases={PHRASES}
      defaultPeriod={DEFAULT_PERIOD}
      onSelect={onSelect}
    />,
  );
  return onSelect;
}

function signedIn(presets: PresetRow[], current: Scope = SCOPE) {
  vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
  const get = vi.spyOn(client, "apiGet").mockResolvedValue(presets as never);
  const post = vi.spyOn(client, "apiPost").mockResolvedValue({ preset_id: 9 } as never);
  const del = vi.spyOn(client, "apiDelete").mockResolvedValue(undefined);
  const onSelect = renderMenu(current);
  return { onSelect, get, post, del };
}

const open = () => userEvent.click(screen.getByRole("button", { name: "Saved views" }));
const menu = () => screen.getByRole("dialog", { name: "Saved views" });

describe("PresetMenu (anonymous)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("replaces the native title on the login hint with a keyboard-reachable Tooltip", () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: null } as never);
    renderMenu();
    const hint = screen.getByText("Saved views");
    expect(hint).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.focusIn(hint);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Sign in to save");
    fireEvent.focusOut(hint);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});

describe("PresetMenu (signed in)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("heads the menu with its name and how many views it holds", async () => {
    signedIn([{ preset_id: 1, agency_id: 1, name: "Weekday mornings", range_ctx: SAVED }]);
    await open();
    expect(await within(menu()).findByText("1 view")).toBeInTheDocument();
  });

  it("explains saved views and offers to save the current conditions in place when there are none", async () => {
    signedIn([]);
    await open();
    expect(
      await screen.findByText(
        "Save the conditions you look at often under a name, and next time you can come straight back to them from here.",
      ),
    ).toBeInTheDocument();
    expect(within(menu()).getByText("0 views")).toBeInTheDocument();
    expect(within(menu()).getByText("all routes, 1/1 – 1/30, every day, all hours, within 1 min")).toBeInTheDocument();
    const name = within(menu()).getByRole("textbox", { name: "View name" });
    expect(name).toHaveValue("all routes, 1/1 – 1/30");
    expect(name).toHaveFocus();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("suggests a name from the conditions changed from the default", async () => {
    signedIn([], { ...SCOPE, dow: "weekday", time_band: "morning" });
    await open();
    expect(await screen.findByRole("textbox", { name: "View name" })).toHaveValue("weekdays, Morning (05–09)");
  });

  it("saves the current scope under the typed name", async () => {
    const { post } = signedIn([]);
    const user = userEvent.setup();
    await open();
    const name = await screen.findByRole("textbox", { name: "View name" });
    await user.clear(name);
    await user.type(name, "My view");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(post).toHaveBeenCalledWith("/api/me/presets", { agency_id: 1, name: "My view", range_ctx: SCOPE });
  });

  it("won't save a blank name", async () => {
    signedIn([]);
    const user = userEvent.setup();
    await open();
    await user.clear(await screen.findByRole("textbox", { name: "View name" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("says in words that a name is taken", async () => {
    signedIn([]);
    vi.spyOn(client, "apiPost").mockRejectedValue(new client.ApiError(409, JSON.stringify({ detail: "name already used" })));
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A view with this name already exists. Choose another name.");
  });

  it("lists each view with its conditions, marks the one on screen, and applies the one picked", async () => {
    const { onSelect } = signedIn([
      { preset_id: 1, agency_id: 1, name: "Weekday mornings", range_ctx: SAVED },
      { preset_id: 2, agency_id: 1, name: "Everything", range_ctx: SCOPE },
    ]);
    await open();
    const everything = await screen.findByRole("button", { name: "Everything" });
    expect(everything).toHaveAttribute("aria-current", "true");
    expect(everything).toHaveAccessibleDescription("all routes, 1/1 – 1/30, every day, all hours, within 1 min");
    const weekday = screen.getByRole("button", { name: "Weekday mornings" });
    expect(weekday).not.toHaveAttribute("aria-current");
    expect(weekday).toHaveAccessibleDescription("all routes, 1/1 – 1/30, weekdays, all hours, within 1 min");
    await userEvent.click(weekday);
    expect(onSelect).toHaveBeenCalledWith(SAVED);
    expect(screen.queryByRole("button", { name: "Weekday mornings" })).toBeNull();
    expect(screen.getByRole("button", { name: "Saved views" })).toHaveFocus();
  });

  it("describes a view saved before some conditions existed as picking it would apply it", async () => {
    signedIn([{ preset_id: 1, agency_id: 1, name: "Old", range_ctx: { dow: "weekday" } }]);
    await open();
    expect(await screen.findByRole("button", { name: "Old" })).toHaveAccessibleDescription(
      "all routes, 1/1 – 1/30, weekdays, all hours, within 1 min",
    );
  });

  it("deletes a view with its own button and refreshes the list", async () => {
    const { get, del } = signedIn([
      { preset_id: 1, agency_id: 1, name: "Weekday mornings", range_ctx: SAVED },
      { preset_id: 2, agency_id: 1, name: "Everything", range_ctx: SCOPE },
    ]);
    await open();
    get.mockResolvedValue([{ preset_id: 2, agency_id: 1, name: "Everything", range_ctx: SCOPE }] as never);
    await userEvent.click(await screen.findByRole("button", { name: "Delete Weekday mornings" }));
    expect(del).toHaveBeenCalledWith("/api/me/presets/1");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Weekday mornings" })).toBeNull());
    expect(screen.getByRole("button", { name: "Everything" })).toBeInTheDocument();
    expect(menu().contains(document.activeElement)).toBe(true);
  });

  it("opens the save form from the footer once views exist", async () => {
    signedIn([{ preset_id: 1, agency_id: 1, name: "Weekday mornings", range_ctx: SAVED }]);
    await open();
    expect(await screen.findByRole("button", { name: "Weekday mornings" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "View name" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save current conditions" }));
    expect(screen.getByRole("textbox", { name: "View name" })).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Save current conditions" })).toBeNull();
  });

  it("closes on Escape and hands focus back to its button", async () => {
    signedIn([]);
    await open();
    await screen.findByRole("textbox", { name: "View name" });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Saved views" })).toHaveFocus();
  });

  it("claims no empty list while the saved views are still loading", async () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
    vi.spyOn(client, "apiGet").mockReturnValue(new Promise(() => {}) as never);
    renderMenu();
    await open();
    expect(screen.queryByText(/0 views|Save the conditions/)).toBeNull();
  });

  it("says when the saved views couldn't be loaded", async () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
    vi.spyOn(client, "apiGet").mockRejectedValue(new Error("network down"));
    renderMenu();
    await open();
    expect(await screen.findByText("Couldn't load your saved views.")).toBeInTheDocument();
    expect(screen.queryByText(/Save the conditions/)).toBeNull();
  });
});
