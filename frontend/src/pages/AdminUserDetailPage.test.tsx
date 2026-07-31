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

const apiGetMock = vi.hoisted(() => vi.fn().mockResolvedValue(mockDetail));
const apiPatchMock = vi.hoisted(() => vi.fn().mockResolvedValue({ ...mockDetail, role: "admin" }));
const apiDeleteMock = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  formatApiError: () => "error",
}));

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
    apiPatchMock.mockClear();
    apiDeleteMock.mockClear();
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
    renderPage();
    await screen.findByText("a@b.com");
    await user.selectOptions(screen.getByLabelText("Role"), "admin");
    expect(apiPatchMock).toHaveBeenCalledWith("/api/admin/users/1", { role: "admin" });
    await vi.waitFor(() => expect(apiGetMock).toHaveBeenCalledTimes(2));
  });

  it("suspends via the actions panel", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Suspend" }));
    expect(apiPatchMock).toHaveBeenCalledWith("/api/admin/users/1", { suspended: true });
  });

  it("deletes via the actions panel after confirming", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiDeleteMock).toHaveBeenCalledWith("/api/admin/users/1");
  });

  it("does not delete when confirm is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiDeleteMock).not.toHaveBeenCalled();
  });

  it("shows a guard-rejection error inline", async () => {
    apiPatchMock.mockRejectedValueOnce(new Error("would leave no admins"));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("a@b.com");
    await user.click(screen.getByRole("button", { name: "Suspend" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("error");
  });
});
