import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { AccountPage } from "./AccountPage";

const mockMutate = vi.fn();
let mockLogoutState: { isPending: boolean; isError: boolean } = { isPending: false, isError: false };

const mockSession = {
  user_id: 1,
  email: "yo@example.com",
  name: "Yo",
  avatar_url: null,
  role: "user" as const,
  identities: [],
};

vi.mock("../api/auth", () => ({
  useSession: () => ({ data: mockSession, isLoading: false }),
  useLogout: () => ({ mutate: mockMutate, ...mockLogoutState }),
}));

const mockApiGet = vi.fn().mockResolvedValue([]);
vi.mock("../api/client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

function renderAccount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AccountPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AccountPage logout", () => {
  beforeEach(() => {
    mockMutate.mockReset();
    mockLogoutState = { isPending: false, isError: false };
  });

  it("calls the logout mutation when the button is clicked", async () => {
    const user = userEvent.setup();
    renderAccount();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(mockMutate).toHaveBeenCalled();
  });

  it("shows an inline error message when the logout mutation fails — the button previously failed completely silently", () => {
    mockLogoutState = { isPending: false, isError: true };
    renderAccount();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't sign out. Please check your connection and try again.");
  });

  it("shows no error message when the mutation hasn't failed", () => {
    renderAccount();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("formats the session's last-seen timestamp in the active UI language, not a hardcoded ja-JP", async () => {
    mockApiGet.mockResolvedValue([
      { sid_prefix: "abc123", user_agent: "Chrome", ip: "1.2.3.4", created_at: "2026-01-01T00:00:00Z", last_seen_at: "2026-01-02T03:04:00Z" },
    ]);
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
    await i18n.changeLanguage("en");
    renderAccount();
    await screen.findByText("Chrome");
    expect(localeSpy).toHaveBeenCalledWith("en");
    localeSpy.mockRestore();
  });
});
