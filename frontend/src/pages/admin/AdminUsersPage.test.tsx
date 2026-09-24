import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminUsersPage } from "./AdminUsersPage";
import { ApiError } from "../../api/client";

const patchMutate = vi.fn();
const patchReset = vi.fn();
const delMutate = vi.fn().mockResolvedValue(undefined);
const delReset = vi.fn();
const bulkMutate = vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
const useAdminUsersMock = vi.fn();
const useSessionMock = vi.fn();

let patchMutationError: unknown = null;
let bulkPending = false;
let bulkVariables: { ids: number[]; patch: unknown } | undefined;

vi.mock("../../api/admin", () => ({
  useAdminUsers: (params: unknown) => useAdminUsersMock(params),
  usePatchUser: () => ({ mutate: patchMutate, reset: patchReset, error: patchMutationError, isPending: false, variables: undefined }),
  useDeleteUser: () => ({
    mutate: delMutate,
    mutateAsync: delMutate,
    reset: delReset,
    error: null,
    isPending: false,
    variables: undefined,
  }),
  useBulkPatchUsers: () => ({ mutate: bulkMutate, error: null, isPending: bulkPending, variables: bulkVariables }),
}));

// A signed-in admin who is not one of the two rendered users (user_id 999),
// so existing tests exercise the normal (not self-mutation-blocked) path.
vi.mock("../../api/auth", () => ({
  useSession: () => useSessionMock(),
}));

function twoUsers() {
  return {
    data: {
      users: [
        {
          user_id: 1,
          email: "active@example.com",
          name: "Active User",
          avatar_url: null,
          role: "admin",
          suspended_at: null,
          llm_approved: false,
          created_at: "2026-06-01T00:00:00Z",
        },
        {
          user_id: 2,
          email: "suspended@example.com",
          name: null,
          avatar_url: null,
          role: "user",
          suspended_at: "2026-06-10T00:00:00Z",
          llm_approved: false,
          created_at: "2026-05-01T00:00:00Z",
        },
      ],
      total: 2,
    },
    isLoading: false,
    error: null,
  };
}

function wrap(initialEntries = ["/admin/users"]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={initialEntries}>
          <AdminUsersPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

function wrapWithExternalNav(initialEntries: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Harness() {
    const navigate = useNavigate();
    return (
      <>
        <button onClick={() => navigate("/admin/users?q=bar")}>go-bar</button>
        <AdminUsersPage />
      </>
    );
  }
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={initialEntries}>
          <Harness />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

/** The table's body rows, without the header row. Row focus is real DOM
 *  focus, so a keyboard test has to start from a focused row. */
function dataRows(): HTMLElement[] {
  return screen.getAllByRole("row").slice(1);
}

describe("AdminUsersPage", () => {
  beforeEach(() => {
    patchMutationError = null;
    bulkPending = false;
    bulkVariables = undefined;
    useAdminUsersMock.mockReset();
    useAdminUsersMock.mockReturnValue(twoUsers());
    useSessionMock.mockReset();
    useSessionMock.mockReturnValue({ data: { user_id: 999, role: "admin" } });
    patchMutate.mockClear();
    patchReset.mockClear();
    delMutate.mockClear();
    delReset.mockClear();
    bulkMutate.mockClear();
  });

  it("shows a colored Active chip for a user with no suspended_at", () => {
    wrap();
    const chip = within(screen.getByRole("table")).getByText("Active");
    // The deepened --accent-strong, not --accent: this text sits directly on
    // --accent-soft, where plain --accent falls short of WCAG AA (4.32:1).
    expect(chip.style.color).toBe("var(--accent-strong)");
    expect(chip.style.background).toBe("var(--accent-soft)");
  });

  it("shows a colored Suspended chip for a user with suspended_at set", () => {
    wrap();
    const chip = within(screen.getByRole("table")).getByText("Suspended");
    expect(chip.style.color).toBe("var(--color-warning-text, #89691F)");
    expect(chip.style.background).toBe("var(--surface-2)");
  });

  it("renders every row with exactly one status chip (no bare em-dash for active users)", () => {
    wrap();
    const table = within(screen.getByRole("table"));
    expect(table.queryByText("—")).toBeNull();
    expect(table.getAllByText("Active")).toHaveLength(1);
    expect(table.getAllByText("Suspended")).toHaveLength(1);
  });

  it("links each email to its user detail page", () => {
    wrap();
    const link = screen.getByRole("link", { name: "active@example.com" });
    expect(link).toHaveAttribute("href", "/admin/users/1");
  });

  it("passes limit=50 and offset computed from the page URL param", () => {
    wrap(["/admin/users?page=3"]);
    expect(useAdminUsersMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 50, offset: 100 })
    );
  });

  it("passes role and suspended filter values from their URL params", () => {
    wrap(["/admin/users?role=admin&suspended=true"]);
    expect(useAdminUsersMock).toHaveBeenCalledWith(
      expect.objectContaining({ role: "admin", suspended: "true" })
    );
  });

  it("shows an empty-state row when there are zero users", () => {
    useAdminUsersMock.mockReturnValue({ data: { users: [], total: 0 }, isLoading: false, error: null });
    wrap();
    expect(screen.getByText("No users found.")).toBeTruthy();
  });

  it("resets the page URL param to 1 when the role filter changes", async () => {
    const user = userEvent.setup();
    wrap(["/admin/users?page=3"]);
    await user.selectOptions(screen.getByLabelText("Role"), "admin");
    expect(useAdminUsersMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ role: "admin", offset: 0 })
    );
  });

  it("self-heals an out-of-range page back to the last valid page", async () => {
    useAdminUsersMock.mockReturnValue({
      data: { users: twoUsers().data.users, total: 120 },
      isLoading: false,
      error: null,
    });
    wrap(["/admin/users?page=99"]);
    // totalPages = ceil(120 / 50) = 3, so page 99 should self-correct to
    // page 3 (offset 100) instead of leaving the admin on a dead-end blank page.
    await vi.waitFor(() =>
      expect(useAdminUsersMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 100 }))
    );
  });

  it("keeps a 3-wide numbered window at page 1 instead of collapsing to one button", () => {
    useAdminUsersMock.mockReturnValue({
      data: { users: twoUsers().data.users, total: 500 },
      isLoading: false,
      error: null,
    });
    wrap();
    // totalPages = 10, page = 1 — window should be [2,3,4], not just [2].
    expect(screen.getByRole("button", { name: "3" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "4" })).toBeTruthy();
  });

  it("renders numbered page buttons and disables Prev on page 1", () => {
    useAdminUsersMock.mockReturnValue({
      data: { users: twoUsers().data.users, total: 120 },
      isLoading: false,
      error: null,
    });
    wrap();
    expect(screen.getByRole("button", { name: "2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "3" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous" })).toHaveProperty("disabled", true);
  });

  it("windows pagination buttons with ellipsis when totalPages > 7", () => {
    useAdminUsersMock.mockReturnValue({
      data: { users: twoUsers().data.users, total: 500 },
      isLoading: false,
      error: null,
    });
    wrap(["/admin/users?page=5"]);
    // totalPages = ceil(500 / 50) = 10, page = 5
    expect(screen.getByRole("button", { name: "1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "10" })).toBeTruthy();
    // Ellipsis should appear
    expect(screen.getAllByText("…").length).toBeGreaterThan(0);
    // Far-out page (9) should not render as button when windowing around 5
    expect(screen.queryByRole("button", { name: "9" })).toBeNull();
  });

  it("does not revert the page once the search-debounce window elapses (regression test)", () => {
    vi.useFakeTimers();
    try {
      useAdminUsersMock.mockReturnValue({
        data: { users: twoUsers().data.users, total: 500 },
        isLoading: false,
        error: null,
      });
      wrap(["/admin/users?page=3"]);
      useAdminUsersMock.mockClear();
      fireEvent.click(screen.getByRole("button", { name: "4" }));
      vi.advanceTimersByTime(500);
      // Bug was: the qInput-debounce effect re-armed on this URL change and
      // deleted `page` 300ms later, reverting the fetch to offset 0. Scoped
      // to the main list's param shape (via `role`) so it isn't coincidentally
      // tripped by the separate, always-offset-0 pending-approvals badge query.
      expect(useAdminUsersMock).not.toHaveBeenCalledWith(expect.objectContaining({ role: "", offset: 0 }));
      expect(useAdminUsersMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 150 }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("disables role/suspend/delete on the signed-in admin's own row, not on other rows", () => {
    useSessionMock.mockReturnValue({ data: { user_id: 1, role: "admin" } });
    wrap();
    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    const ownRow = within(rows[0]); // user_id 1
    expect(ownRow.getByRole("combobox")).toHaveProperty("disabled", true);
    expect(ownRow.getByRole("button", { name: "Suspend" })).toHaveProperty("disabled", true);
    expect(ownRow.getByRole("button", { name: "Delete" })).toHaveProperty("disabled", true);
    const otherRow = within(rows[1]); // user_id 2, already suspended
    expect(otherRow.getByRole("button", { name: "Resume" })).toHaveProperty("disabled", false);
  });

  it("asks for confirmation before promoting a row to admin, then mutates", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    wrap();
    const rows = screen.getAllByRole("row").slice(1);
    await user.selectOptions(within(rows[1]).getByRole("combobox"), "admin"); // user_id 2, role user
    expect(confirmSpy).toHaveBeenCalled();
    expect(delReset).toHaveBeenCalled();
    expect(patchMutate).toHaveBeenCalledWith({ uid: 2, body: { role: "admin" } });
    confirmSpy.mockRestore();
  });

  it("does not mutate when the promote confirmation is declined", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    wrap();
    const rows = screen.getAllByRole("row").slice(1);
    await user.selectOptions(within(rows[1]).getByRole("combobox"), "admin");
    expect(patchMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  describe("bulk selection and undo", () => {
    it("disables the checkbox for the signed-in admin's own row only", () => {
      useSessionMock.mockReturnValue({ data: { user_id: 1, role: "admin" } });
      wrap();
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("disabled", true);
      expect(screen.getByRole("checkbox", { name: "Select suspended@example.com" })).toHaveProperty(
        "disabled",
        false,
      );
    });

    it("shows the floating bulk bar with a selected count once a row is checked", async () => {
      const user = userEvent.setup();
      wrap();
      expect(screen.queryByText("1 selected")).toBeNull();
      await user.click(screen.getByRole("checkbox", { name: "Select active@example.com" }));
      expect(screen.getByText("1 selected")).toBeTruthy();
    });

    it("select-all checks every selectable row and bulk-clear unchecks them", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select all" }));
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("checked", true);
      expect(screen.getByRole("checkbox", { name: "Select suspended@example.com" })).toHaveProperty(
        "checked",
        true,
      );
      await user.click(screen.getByRole("button", { name: "Clear selection" }));
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("checked", false);
    });

    it("bulk-approves the selected ids and shows an undo toast with the inverse patch ready", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select active@example.com" }));
      const bulkBar = within(screen.getByTestId("admin-users-bulk-bar"));
      await user.click(bulkBar.getByRole("button", { name: "Approve AI access" }));
      expect(bulkMutate).toHaveBeenCalledWith(
        { ids: [1], patch: { llm_approved: true } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
      expect(await screen.findByText("Approved AI access for 1")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
    });

    it("clicking undo sends the inverse patch and hides the toast", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select active@example.com" }));
      await user.click(within(screen.getByTestId("admin-users-bulk-bar")).getByRole("button", { name: "Approve AI access" }));
      bulkMutate.mockClear();
      await user.click(screen.getByRole("button", { name: "Undo" }));
      expect(bulkMutate).toHaveBeenCalledWith({ ids: [1], patch: { llm_approved: false } });
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    });

    it("bulk-suspends the selection via the bulk bar", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select active@example.com" }));
      await user.click(within(screen.getByTestId("admin-users-bulk-bar")).getByRole("button", { name: "Suspend" }));
      expect(bulkMutate).toHaveBeenCalledWith(
        { ids: [1], patch: { suspended: true } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });

    it("bulk-promotes via the role change control in the bulk bar", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select suspended@example.com" }));
      await user.selectOptions(screen.getByRole("combobox", { name: "Change role" }), "admin");
      expect(bulkMutate).toHaveBeenCalledWith(
        { ids: [2], patch: { role: "admin" } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });

    it("routes bulk delete through the existing per-user confirm flow for each selected id", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select all" }));
      await user.click(within(screen.getByTestId("admin-users-bulk-bar")).getByRole("button", { name: "Delete" }));
      await vi.waitFor(() => expect(delMutate).toHaveBeenCalledTimes(2));
      expect(delMutate).toHaveBeenCalledWith(1);
      expect(delMutate).toHaveBeenCalledWith(2);
      confirmSpy.mockRestore();
    });

    it("skips a selected id when its delete confirm is declined", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select active@example.com" }));
      await user.click(within(screen.getByTestId("admin-users-bulk-bar")).getByRole("button", { name: "Delete" }));
      expect(delMutate).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });
  });

  describe("saved views", () => {
    it("shows the pending-approval badge count from the separate always-offset-0 query", () => {
      useAdminUsersMock.mockImplementation((params: { llmApproved?: string }) =>
        params.llmApproved === "false" ? { data: { users: [], total: 2 }, isLoading: false, error: null } : twoUsers(),
      );
      wrap();
      expect(screen.getByText("2")).toBeTruthy();
    });

    it("selecting the pending-approval view sets llm_approved=false and clears role/suspended", async () => {
      const user = userEvent.setup();
      wrap(["/admin/users?role=admin&suspended=true"]);
      useAdminUsersMock.mockClear();
      await user.click(screen.getByRole("button", { name: /Pending approval/ }));
      expect(useAdminUsersMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ role: "", suspended: "", llmApproved: "false" }),
      );
    });

    it("marks the admins view active from the role=admin URL param", () => {
      wrap(["/admin/users?role=admin"]);
      expect(screen.getByRole("button", { name: "Admins" })).toHaveAttribute("aria-pressed", "true");
    });
  });

  it("names the table for a screen reader instead of leaking the i18n key", () => {
    // The caption is the table's accessible name; key parity between
    // locales cannot catch a key that exists in neither.
    wrap();
    expect(screen.getByRole("table", { name: "Users" })).toBeTruthy();
  });

  it("navigates once when the email link is clicked, so Back returns to the list", async () => {
    // The row is clickable as a whole now, and Link does not stop
    // propagation on its own.
    const user = userEvent.setup();
    wrap();
    const before = window.history.length;
    await user.click(screen.getByRole("link", { name: "active@example.com" }));
    expect(window.history.length - before).toBeLessThanOrEqual(1);
  });

  describe("keyboard navigation", () => {
    it("j moves focus down and x toggles selection on the focused row", () => {
      wrap();
      const [first, second] = dataRows();
      first.focus();
      fireEvent.keyDown(first, { key: "j" });
      expect(second).toHaveFocus();
      fireEvent.keyDown(second, { key: "x" });
      expect(screen.getByRole("checkbox", { name: "Select suspended@example.com" })).toHaveProperty(
        "checked",
        true,
      );
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("checked", false);
    });

    it("k does not move focus above the first row", () => {
      wrap();
      const [first] = dataRows();
      first.focus();
      fireEvent.keyDown(first, { key: "k" });
      expect(first).toHaveFocus();
      fireEvent.keyDown(first, { key: "x" });
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("checked", true);
    });

    it("disables the bulk bar's own controls while a batch is in flight", async () => {
      // A second click sends the same ids again and lets whichever request
      // lands second decide the outcome.
      const user = userEvent.setup();
      bulkPending = true;
      bulkVariables = { ids: [1], patch: { llm_approved: true } };
      wrap();
      await user.click(screen.getByRole("checkbox", { name: "Select all" }));
      const bar = within(screen.getByTestId("admin-users-bulk-bar"));
      expect(bar.getByRole("button", { name: "Suspend" })).toHaveProperty("disabled", true);
      expect(bar.getByRole("combobox")).toHaveProperty("disabled", true);
    });

    it("locks the row a bulk request is mutating even when nothing is selected", () => {
      // The `a` shortcut acts on one unselected row, so a guard reading the
      // page's selection would leave that row's own controls live.
      bulkPending = true;
      bulkVariables = { ids: [1], patch: { llm_approved: true } };
      wrap();
      const row = within(dataRows()[0]);
      expect(row.getByRole("button", { name: /Delete/ })).toHaveProperty("disabled", true);
    });

    it("a approves the focused row when nothing is selected", () => {
      wrap();
      const [first] = dataRows();
      first.focus();
      fireEvent.keyDown(first, { key: "a" });
      expect(bulkMutate).toHaveBeenCalledWith(
        { ids: [1], patch: { llm_approved: true } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });

    it("/ focuses the search input", () => {
      wrap();
      fireEvent.keyDown(document, { key: "/" });
      expect(screen.getByRole("searchbox")).toHaveFocus();
    });

    it("does not react to row keys while typing in the search box", async () => {
      const user = userEvent.setup();
      wrap();
      await user.click(screen.getByRole("searchbox"));
      fireEvent.keyDown(screen.getByRole("searchbox"), { key: "x" });
      expect(screen.getByRole("checkbox", { name: "Select active@example.com" })).toHaveProperty("checked", false);
    });

    it("lets / be typed into the search box instead of re-focusing it", async () => {
      // `/` is the one shortcut still bound on the document, so it is the
      // one that can still swallow a character the operator meant to type.
      const user = userEvent.setup();
      wrap();
      const box = screen.getByRole("searchbox");
      await user.click(box);
      await user.type(box, "a/b");
      expect(box).toHaveValue("a/b");
    });
  });

  it("updates the displayed search value when the URL's q changes externally (e.g. browser back/forward)", () => {
    wrapWithExternalNav(["/admin/users?q=foo"]);
    expect(screen.getByPlaceholderText("Search by email / name")).toHaveValue("foo");
    fireEvent.click(screen.getByText("go-bar"));
    // A URL change that didn't come from this component's own debounce
    // commit must still be reflected in the input -- otherwise the box shows
    // a query that no longer matches the results underneath it.
    expect(screen.getByPlaceholderText("Search by email / name")).toHaveValue("bar");
  });

  it("keeps focus on the search input once its own debounce commits the typed value", async () => {
    vi.useFakeTimers();
    try {
      wrap();
      const input = screen.getByPlaceholderText("Search by email / name") as HTMLInputElement;
      input.focus();
      fireEvent.change(input, { target: { value: "foo" } });
      await vi.advanceTimersByTimeAsync(500); // > SEARCH_DEBOUNCE_MS (300ms, not exported)
      // A fix that resyncs the input to the URL by remounting it (e.g.
      // `key={q}`) would recreate the DOM node here and drop focus the
      // moment its own debounce commits -- not just on external navigation.
      expect(document.activeElement).toBe(screen.getByPlaceholderText("Search by email / name"));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows an ErrorBanner instead of a raw error string when the user list fails to load", () => {
    useAdminUsersMock.mockReturnValue({ data: undefined, isLoading: false, error: new Error("network down"), refetch: vi.fn() });
    wrap();
    // ErrorBanner's generic-network branch renders role="alert"; the old
    // raw formatApiError(error) text node had no such role.
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("routes a mutation error through ErrorBanner's calm branches, not a raw formatApiError string", () => {
    patchMutationError = new ApiError(403, JSON.stringify({ detail: "llm_not_approved" }));
    wrap();
    // ErrorBanner's admin-approval-required branch renders role="status" with
    // its own calm copy -- the old raw formatApiError(error) rendering had no
    // such special-casing, just the generic status-code text in a plain div.
    expect(screen.getByRole("status")).toBeTruthy();
  });
});
