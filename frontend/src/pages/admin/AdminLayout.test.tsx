import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminLayout } from "./AdminLayout";

let mockUsers: { data?: { users: { llm_approved: boolean; suspended_at: string | null }[]; total: number } };

vi.mock("../../api/admin", () => ({ useAdminUsers: () => mockUsers }));

function wrap(path = "/admin") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[path]}>
        <AdminLayout />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

describe("AdminLayout sub-nav", () => {
  beforeEach(() => {
    mockUsers = { data: { users: [], total: 0 } };
  });

  it("groups the destinations under section headings", () => {
    wrap();
    const nav = screen.getByRole("navigation", { name: i18n.t("admin.nav.label") });
    for (const key of ["operations", "people", "reference"] as const) {
      expect(within(nav).getByText(i18n.t(`admin.nav.group.${key}`))).toBeInTheDocument();
    }
  });

  it("hides a group that has no destinations yet rather than showing a bare heading", () => {
    wrap();
    const nav = screen.getByRole("navigation", { name: i18n.t("admin.nav.label") });
    expect(within(nav).queryByText(i18n.t("admin.nav.group.governance"))).not.toBeInTheDocument();
  });

  it("puts the control board first, linking at the admin root", () => {
    wrap();
    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveAccessibleName(new RegExp(i18n.t("admin.nav.board")));
    expect(links[0]).toHaveAttribute("href", "/admin");
  });

  it("badges the people group with the count of users awaiting AI approval", () => {
    mockUsers = {
      data: {
        users: [
          { llm_approved: false, suspended_at: null },
          { llm_approved: false, suspended_at: null },
          { llm_approved: true, suspended_at: null },
          { llm_approved: false, suspended_at: "2026-01-01T00:00:00Z" },
        ],
        total: 4,
      },
    };
    wrap();
    const users = screen.getByRole("link", { name: new RegExp(i18n.t("admin.nav.users")) });
    expect(within(users).getByTestId("nav-badge")).toHaveTextContent("2");
  });

  it("shows no badge when nothing is waiting", () => {
    wrap();
    const users = screen.getByRole("link", { name: new RegExp(i18n.t("admin.nav.users")) });
    expect(within(users).queryByTestId("nav-badge")).not.toBeInTheDocument();
  });

  it("survives the users list being unavailable", () => {
    mockUsers = { data: undefined };
    wrap();
    expect(screen.getByRole("link", { name: new RegExp(i18n.t("admin.nav.users")) })).toBeInTheDocument();
  });
});
