import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { AdminUserDetailPage } from "./AdminUserDetailPage";

const mockDetail = vi.hoisted(() => ({
  user_id: 1,
  email: "a@b.com",
  name: "A B",
  avatar_url: null,
  role: "user" as const,
  suspended_at: null,
  created_at: "2026-01-01T00:00:00Z",
  identities: [],
  recent_events: [],
}));

// A signed-in admin viewing someone else's record (user_id 999 !== 1), so
// existing tests exercise the normal (not self-mutation-blocked) path.
const mockSession = vi.hoisted(() => ({
  user_id: 999,
  email: "admin@example.com",
  name: "Admin",
  avatar_url: null,
  role: "admin" as const,
  identities: [],
}));

const apiGetMock = vi.hoisted(() => vi.fn().mockResolvedValue(mockDetail));
const apiGetOrNullMock = vi.hoisted(() => vi.fn().mockResolvedValue(mockSession));
const apiPatchMock = vi.hoisted(() => vi.fn().mockResolvedValue({ ...mockDetail, role: "admin" }));
const apiDeleteMock = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiGetOrNull: apiGetOrNullMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  formatApiError: () => "error",
}));

const navigateMock = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/admin/users/1"]}>
          <Routes>
            <Route path="/admin/users/:uid" element={<AdminUserDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminUserDetailPage", () => {
  beforeEach(() => {
    apiGetMock.mockClear();
    apiGetOrNullMock.mockClear();
    apiGetOrNullMock.mockResolvedValue(mockSession);
    apiPatchMock.mockClear();
    apiDeleteMock.mockClear();
    navigateMock.mockClear();
  });

  it("formats the created-at timestamp in the active UI language, not a hardcoded ja-JP", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
    await i18n.changeLanguage("en");
    renderPage();
    await screen.findByText("a@b.com");
    expect(localeSpy).toHaveBeenCalledWith("en");
    localeSpy.mockRestore();
  });

  it("has a back-to-users link", async () => {
    renderPage();
    await screen.findByText("a@b.com");
    expect(screen.getByRole("link", { name: /back to users/i })).toHaveAttribute("href", "/admin/users");
  });

  it("changes role via the actions panel and refetches the detail query", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("a@b.com");
    await user.selectOptions(screen.getByLabelText("Role"), "admin");
    expect(apiPatchMock).toHaveBeenCalledWith("/api/admin/users/1", { role: "admin" });
    await vi.waitFor(() => expect(apiGetMock).toHaveBeenCalledTimes(2));
    confirmSpy.mockRestore();
  });

  it("suspends via the actions panel", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Suspend" }));
    expect(apiPatchMock).toHaveBeenCalledWith("/api/admin/users/1", { suspended: true });
  });

  it("deletes via the actions panel after confirming, then navigates back to the list", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiDeleteMock).toHaveBeenCalledWith("/api/admin/users/1");
    await vi.waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/admin/users"));
    confirmSpy.mockRestore();
  });

  it("does not delete when confirm is declined", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiDeleteMock).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("shows a guard-rejection error inline", async () => {
    apiPatchMock.mockRejectedValueOnce(new Error("would leave no admins"));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Suspend" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("error");
  });

  it("disables role/suspend/delete when the signed-in admin views their own record", async () => {
    apiGetOrNullMock.mockResolvedValue({ ...mockSession, user_id: 1 }); // same as mockDetail.user_id
    renderPage();
    await screen.findByText("a@b.com");
    expect(await screen.findByText(/can't modify your own account/i)).toBeTruthy();
    expect(screen.getByLabelText("Role")).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Suspend" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Delete" })).toHaveProperty("disabled", true);
  });
});
