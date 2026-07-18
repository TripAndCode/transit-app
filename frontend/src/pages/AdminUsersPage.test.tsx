import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { AdminUsersPage } from "./AdminUsersPage";

const patchMutate = vi.fn();
const delMutate = vi.fn();

vi.mock("../api/admin", () => ({
  useAdminUsers: () => ({
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
  }),
  usePatchUser: () => ({ mutate: patchMutate, error: null }),
  useDeleteUser: () => ({ mutate: delMutate, error: null }),
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AdminUsersPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminUsersPage", () => {
  it("shows a colored Active chip for a user with no suspended_at", () => {
    wrap();
    const chip = screen.getByText("Active");
    expect(chip.style.color).toBe("var(--accent)");
    expect(chip.style.background).toBe("var(--accent-soft)");
  });

  it("shows a colored Suspended chip for a user with suspended_at set", () => {
    wrap();
    const chip = screen.getByText("Suspended");
    expect(chip.style.color).toBe("var(--color-warning, #C99A2E)");
    expect(chip.style.background).toBe("var(--surface-2)");
  });

  it("renders every row with exactly one status chip (no bare em-dash for active users)", () => {
    wrap();
    expect(screen.queryByText("—")).toBeNull();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("Suspended")).toBeTruthy();
  });

  it("links each email to its user detail page", () => {
    wrap();
    const link = screen.getByRole("link", { name: "active@example.com" });
    expect(link).toHaveAttribute("href", "/admin/users/1");
  });
});
