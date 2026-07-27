import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

vi.mock("../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue(mockDetail),
  formatApiError: () => "",
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
  it("formats the created-at timestamp in the active UI language, not a hardcoded ja-JP", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
    await i18n.changeLanguage("en");
    renderPage();
    await screen.findByText("a@b.com");
    expect(localeSpy).toHaveBeenCalledWith("en");
    localeSpy.mockRestore();
  });
});
