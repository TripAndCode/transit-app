import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { AdminUsersPage } from "./AdminUsersPage";

const patchMutate = vi.fn();
const delMutate = vi.fn();
const useAdminUsersMock = vi.fn();

vi.mock("../api/admin", () => ({
  useAdminUsers: (params: unknown) => useAdminUsersMock(params),
  usePatchUser: () => ({ mutate: patchMutate, error: null }),
  useDeleteUser: () => ({ mutate: delMutate, error: null }),
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
          created_at: "2026-06-01T00:00:00Z",
        },
        {
          user_id: 2,
          email: "suspended@example.com",
          name: null,
          avatar_url: null,
          role: "user",
          suspended_at: "2026-06-10T00:00:00Z",
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

describe("AdminUsersPage", () => {
  beforeEach(() => {
    useAdminUsersMock.mockReset();
    useAdminUsersMock.mockReturnValue(twoUsers());
  });

  it("shows a colored Active chip for a user with no suspended_at", () => {
    wrap();
    const chip = within(screen.getByRole("table")).getByText("Active");
    expect(chip.style.color).toBe("var(--accent)");
    expect(chip.style.background).toBe("var(--accent-soft)");
  });

  it("shows a colored Suspended chip for a user with suspended_at set", () => {
    wrap();
    const chip = within(screen.getByRole("table")).getByText("Suspended");
    expect(chip.style.color).toBe("var(--color-warning, #C99A2E)");
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
});
