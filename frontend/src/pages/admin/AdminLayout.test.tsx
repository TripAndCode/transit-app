import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminLayout } from "./AdminLayout";

let mockUsers: { data?: { total: number } };
const usersParams = vi.fn();

vi.mock("../../api/admin", () => ({
  useAdminUsers: (params: unknown) => {
    usersParams(params);
    return mockUsers;
  },
}));

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
    mockUsers = { data: { total: 0 } };
  });

  it("groups the destinations under section headings", () => {
    wrap();
    const nav = screen.getByRole("navigation", { name: i18n.t("admin.nav.label") });
    for (const key of ["operations", "people", "reference"] as const) {
      expect(within(nav).getByText(i18n.t(`admin.nav.group.${key}`))).toBeInTheDocument();
    }
  });

  it("shows the governance group now that the audit log gives it a destination", () => {
    wrap();
    const nav = screen.getByRole("navigation", { name: i18n.t("admin.nav.label") });
    expect(within(nav).getByText(i18n.t("admin.nav.group.governance"))).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: new RegExp(i18n.t("admin.nav.audit")) })).toHaveAttribute(
      "href",
      "/admin/audit",
    );
  });

  it("puts the control board first, linking at the admin root", () => {
    wrap();
    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveAccessibleName(new RegExp(i18n.t("admin.nav.board")));
    expect(links[0]).toHaveAttribute("href", "/admin");
  });

  it("badges the people group with the count of users awaiting AI approval", () => {
    mockUsers = { data: { total: 2 } };
    wrap();
    const users = screen.getByRole("link", { name: new RegExp(i18n.t("admin.nav.users")) });
    expect(within(users).getByTestId("nav-badge")).toHaveTextContent("2");
  });

  it("asks the server to count the waiting users rather than filtering a page of them", () => {
    // A page-limited list stops counting once the table outgrows it, and
    // the badge then undercounts with nothing to show that it has.
    mockUsers = { data: { total: 2 } };
    wrap();
    expect(usersParams).toHaveBeenCalledWith(
      expect.objectContaining({ llmApproved: "false", suspended: "false" }),
    );
    const [{ limit }] = usersParams.mock.calls.at(-1) as [{ limit: number }];
    expect(limit).toBeLessThanOrEqual(1);
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
