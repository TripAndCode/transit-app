import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { SCOPE_EXTRAS_NONE, type Scope } from "../api/scope";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as client from "../api/client";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { PresetMenu } from "./PresetMenu";
import { savedViews, type SavedView } from "../api/savedViews";

const SCOPE: Scope = { ...SCOPE_EXTRAS_NONE, from: "2026-01-01", to: "2026-01-30", dow: "all", time_band: "all", service: "all", routes: [] };
const SAVED: Scope = { ...SCOPE, dow: "weekday" };
/** What a reset gives back, so SCOPE changes nothing. */
const DEFAULT_PERIOD = { from: "2026-01-01", to: "2026-01-30" };
const PHRASES = { t: i18n.getFixedT("en"), agencyName: "Aomori", routeLabel: (c: string) => `R${c}` };

function renderMenu(current: Scope = SCOPE) {
  const onSelect = vi.fn();
  renderWithProviders(
    <>
      <PresetMenu
        agencyId={1}
        currentRangeCtx={current}
        phrases={PHRASES}
        defaultPeriod={DEFAULT_PERIOD}
        onSelect={onSelect}
      />
      <button type="button">next control</button>
    </>,
  );
  return onSelect;
}

function seed(rangeCtx: Record<string, unknown>, name = "Weekday mornings"): SavedView {
  const result = savedViews.create(1, name, rangeCtx);
  if (!result.ok) throw new Error("unreachable");
  return result.view;
}

const open = () => userEvent.click(screen.getByRole("button", { name: "Saved views" }));
const menu = () => screen.getByRole("dialog", { name: "Saved views" });

describe("PresetMenu", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("opens the menu for a signed-out visitor with no login gate", async () => {
    renderMenu();
    await open();
    expect(within(menu()).getByText("0 views")).toBeInTheDocument();
  });

  it("names a saved view's stop from the static GTFS even when the current scope has no stop", async () => {
    seed({ ...SAVED, stop: "S1" }, "Stop view");
    const get = vi.spyOn(client, "apiGet").mockResolvedValue({ rows: [{ stop_id: "S1", stop_name: "Central Station" }] } as never);
    renderMenu();
    await open();
    expect(await within(menu()).findByText(/Central Station/)).toBeInTheDocument();
    expect(within(menu()).queryByText(/selected stop/)).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(expect.stringContaining("/api/1/stops"), expect.anything());
  });

  it("heads the menu with its name and how many views it holds", async () => {
    seed(SAVED);
    renderMenu();
    await open();
    expect(await within(menu()).findByText("1 view")).toBeInTheDocument();
  });

  it("explains saved views and offers to save the current conditions in place when there are none", async () => {
    renderMenu();
    await open();
    expect(
      await screen.findByText(
        "Save the conditions you look at often under a name, and next time you can come straight back to them from here. Views stay in this browser.",
      ),
    ).toBeInTheDocument();
    expect(within(menu()).getByText("0 views")).toBeInTheDocument();
    expect(within(menu()).getByText("all routes, 1/1 – 1/30, every day, all hours, within 5 min")).toBeInTheDocument();
    const name = within(menu()).getByRole("textbox", { name: "View name" });
    expect(name).toHaveValue("all routes, 1/1 – 1/30");
    expect(name).toHaveFocus();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("suggests a name from the conditions changed from the default", async () => {
    renderMenu({ ...SCOPE, dow: "weekday", time_band: "morning" });
    await open();
    expect(await screen.findByRole("textbox", { name: "View name" })).toHaveValue("weekdays, Morning (05–09)");
  });

  it("saves the current scope under the typed name and lists it", async () => {
    renderMenu();
    const user = userEvent.setup();
    await open();
    const name = await screen.findByRole("textbox", { name: "View name" });
    await user.clear(name);
    await user.type(name, "My view");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(savedViews.list(1).map((v) => v.name)).toEqual(["My view"]);
    expect(savedViews.list(1)[0].range_ctx).toEqual(SCOPE);
    expect(await screen.findByRole("button", { name: "My view" })).toBeInTheDocument();
  });

  it("won't save a blank name", async () => {
    renderMenu();
    const user = userEvent.setup();
    await open();
    await user.clear(await screen.findByRole("textbox", { name: "View name" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("says in words that a name is taken", async () => {
    seed(SAVED, "My view");
    renderMenu();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Save current conditions" }));
    const name = await screen.findByRole("textbox", { name: "View name" });
    await userEvent.clear(name);
    await userEvent.type(name, "My view");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A view with this name already exists. Choose another name.");
  });

  it("says in words that the agency's view list is full", async () => {
    for (let i = 0; i < 50; i++) seed(SAVED, `view ${i}`);
    renderMenu();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Save current conditions" }));
    const name = await screen.findByRole("textbox", { name: "View name" });
    await userEvent.clear(name);
    await userEvent.type(name, "one too many");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You have reached the limit of saved views for this agency. Delete one to save another.",
    );
  });

  it("reports a refused store instead of silently dropping the view", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    renderMenu();
    await open();
    const name = await screen.findByRole("textbox", { name: "View name" });
    await userEvent.clear(name);
    await userEvent.type(name, "My view");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save this view. Try again.");
  });

  it("lists each view with its conditions, marks the one on screen, and applies the one picked", async () => {
    seed(SAVED, "Weekday mornings");
    seed(SCOPE, "Everything");
    const onSelect = renderMenu();
    await open();
    const everything = await screen.findByRole("button", { name: "Everything" });
    expect(everything).toHaveAttribute("aria-current", "true");
    expect(everything).toHaveAccessibleDescription("all routes, 1/1 – 1/30, every day, all hours, within 5 min");
    const weekday = screen.getByRole("button", { name: "Weekday mornings" });
    expect(weekday).not.toHaveAttribute("aria-current");
    expect(weekday).toHaveAccessibleDescription("all routes, 1/1 – 1/30, weekdays, all hours, within 5 min");
    await userEvent.click(weekday);
    expect(onSelect).toHaveBeenCalledWith(SAVED);
    expect(screen.queryByRole("button", { name: "Weekday mornings" })).toBeNull();
    expect(screen.getByRole("button", { name: "Saved views" })).toHaveFocus();
  });

  it("describes a view saved before some conditions existed as picking it would apply it", async () => {
    seed({ dow: "weekday" }, "Old");
    renderMenu();
    await open();
    expect(await screen.findByRole("button", { name: "Old" })).toHaveAccessibleDescription(
      "all routes, 1/1 – 1/30, weekdays, all hours, within 5 min",
    );
  });

  it("deletes a view with its own button and refreshes the list", async () => {
    seed(SAVED, "Weekday mornings");
    seed(SCOPE, "Everything");
    renderMenu();
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Delete Weekday mornings" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Weekday mornings" })).toBeNull());
    expect(screen.getByRole("button", { name: "Everything" })).toBeInTheDocument();
    expect(savedViews.list(1).map((v) => v.name)).toEqual(["Everything"]);
    expect(menu().contains(document.activeElement)).toBe(true);
  });

  it("opens the save form from the footer once views exist", async () => {
    seed(SAVED, "Weekday mornings");
    renderMenu();
    await open();
    expect(await screen.findByRole("button", { name: "Weekday mornings" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "View name" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save current conditions" }));
    expect(screen.getByRole("textbox", { name: "View name" })).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Save current conditions" })).toBeNull();
  });

  it("closes on Escape and hands focus back to its button", async () => {
    renderMenu();
    await open();
    await screen.findByRole("textbox", { name: "View name" });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Saved views" })).toHaveFocus();
  });

  it("closes when Tab walks focus out of the menu", async () => {
    renderMenu();
    await open();
    await screen.findByRole("textbox", { name: "View name" });
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Save" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "next control" })).toHaveFocus();
  });

  it("clears a failed save's message once a delete succeeds", async () => {
    seed(SAVED, "Weekday mornings");
    renderMenu();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Save current conditions" }));
    const name = await screen.findByRole("textbox", { name: "View name" });
    await userEvent.clear(name);
    await userEvent.type(name, "Weekday mornings");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete Weekday mornings" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("a view saved from elsewhere appears in an already-open mounted menu without a reload", async () => {
    // A second PresetMenu can't stay open alongside this one -- opening it
    // would blur this one's popover closed (see onBlur above) -- so the
    // "elsewhere" stands for any other save path through the same store,
    // such as another mounted menu's own form submit.
    renderMenu();
    await open();
    expect(within(menu()).getByText("0 views")).toBeInTheDocument();
    act(() => {
      const result = savedViews.create(1, "Shared view", SAVED);
      if (!result.ok) throw new Error("unreachable");
    });
    expect(await within(menu()).findByRole("button", { name: "Shared view" })).toBeInTheDocument();
    expect(within(menu()).getByText("1 view")).toBeInTheDocument();
  });
});

describe("PresetMenu (cross-tab storage event)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("picks up a view saved from another tab via the storage event", async () => {
    renderMenu();
    await open();
    expect(within(menu()).getByText("0 views")).toBeInTheDocument();
    localStorage.setItem(
      "transit.savedViews.v1",
      JSON.stringify([{ id: "x", agency_id: 1, name: "From another tab", range_ctx: SAVED, created_at: "2026-01-01T00:00:00.000Z" }]),
    );
    fireEvent(window, new Event("storage"));
    expect(await screen.findByRole("button", { name: "From another tab" })).toBeInTheDocument();
  });
});
