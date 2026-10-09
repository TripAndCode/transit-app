import { describe, it, expect, vi, afterEach } from "vitest";
import { SCOPE_EXTRAS_NONE, type Scope } from "../api/scope";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as client from "../api/client";
import { renderWithProviders } from "../test/renderWithProviders";
import { PresetMenu } from "./PresetMenu";
import * as auth from "../api/auth";

describe("PresetMenu (anonymous)", () => {
  it("replaces the native title on the login hint with a keyboard-reachable Tooltip", () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: null } as never);
    renderWithProviders(
      <PresetMenu
        agencyId={1}
        currentRangeCtx={{ ...SCOPE_EXTRAS_NONE, from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all", service: "all", routes: [] }}
        onSelect={() => {}}
      />,
    );
    const hint = screen.getByText("Saved views");
    expect(hint).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.focusIn(hint);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Sign in to save");
    fireEvent.focusOut(hint);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});

const SCOPE: Scope = { ...SCOPE_EXTRAS_NONE, from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all", service: "all", routes: [] };
const SAVED: Scope = { ...SCOPE, dow: "weekday" };

function signedIn(presets: { preset_id: number; agency_id: number; name: string; range_ctx: unknown }[]) {
  vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
  vi.spyOn(client, "apiGet").mockResolvedValue(presets as never);
  const post = vi.spyOn(client, "apiPost").mockResolvedValue({ preset_id: 9 } as never);
  const onSelect = vi.fn();
  renderWithProviders(<PresetMenu agencyId={1} currentRangeCtx={{ ...SCOPE }} onSelect={onSelect} />);
  return { onSelect, post };
}

describe("PresetMenu (signed in)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists saved views behind one pill and applies the one picked", async () => {
    const { onSelect } = signedIn([{ preset_id: 1, agency_id: 1, name: "Weekday mornings", range_ctx: SAVED }]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Saved views" }));
    await user.click(await screen.findByRole("button", { name: "Weekday mornings" }));
    expect(onSelect).toHaveBeenCalledWith(SAVED);
    expect(screen.queryByRole("button", { name: "Weekday mornings" })).toBeNull();
  });

  it("says when there are no saved views yet", async () => {
    signedIn([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Saved views" }));
    expect(await screen.findByText("No saved views yet")).toBeInTheDocument();
  });

  it("claims no empty list while the saved views are still loading", async () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
    vi.spyOn(client, "apiGet").mockReturnValue(new Promise(() => {}) as never);
    renderWithProviders(<PresetMenu agencyId={1} currentRangeCtx={{ ...SCOPE }} onSelect={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Saved views" }));
    expect(screen.queryByText("No saved views yet")).toBeNull();
  });

  it("says when the saved views couldn't be loaded", async () => {
    vi.spyOn(auth, "useSession").mockReturnValue({ data: { user_id: 1 } } as never);
    vi.spyOn(client, "apiGet").mockRejectedValue(new Error("network down"));
    renderWithProviders(<PresetMenu agencyId={1} currentRangeCtx={{ ...SCOPE }} onSelect={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Saved views" }));
    expect(await screen.findByText("Couldn't load your saved views.")).toBeInTheDocument();
    expect(screen.queryByText("No saved views yet")).toBeNull();
  });

  it("names a new view in a labelled dialog that Escape closes", async () => {
    signedIn([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Saved views" }));
    await user.click(screen.getByRole("button", { name: "Save this view…" }));
    const dialog = screen.getByRole("dialog", { name: "Save this view" });
    expect(dialog).toContainElement(screen.getByRole("textbox", { name: "Name" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Save this view" })).toBeNull();
  });

  it("saves the current scope under the typed name", async () => {
    const { post } = signedIn([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Saved views" }));
    await user.click(screen.getByRole("button", { name: "Save this view…" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "My view");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(post).toHaveBeenCalledWith("/api/me/presets", { agency_id: 1, name: "My view", range_ctx: SCOPE });
  });
});
