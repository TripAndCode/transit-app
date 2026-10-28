import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { formatDateTime } from "../utils/format";
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

let mockSessionLoading = false;
let mockGuest = false;

vi.mock("../api/auth", () => ({
  useSession: () =>
    mockSessionLoading
      ? { data: undefined, isLoading: true }
      : { data: mockGuest ? null : mockSession, isLoading: false },
  useLogout: () => ({ mutate: mockMutate, ...mockLogoutState }),
}));

const mockApiGet = vi.fn().mockResolvedValue([]);
vi.mock("../api/client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiUrl: (path: string) => `https://api.example.test${path}`,
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

function LoginProbe() {
  const { pathname, search } = useLocation();
  return <p>{`at ${pathname}${search}`}</p>;
}

describe("AccountPage for a guest", () => {
  beforeEach(() => {
    mockGuest = true;
    mockApiGet.mockClear();
  });
  afterEach(() => {
    mockGuest = false;
  });

  function renderAsGuest() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={["/me"]}>
            <Routes>
              <Route path="/me" element={<AccountPage />} />
              <Route path="/login" element={<LoginProbe />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </I18nextProvider>,
    );
  }

  it("sends a guest to sign in with the way back to the account page", () => {
    renderAsGuest();
    expect(screen.getByText(`at /login?next=${encodeURIComponent("/me")}`)).toBeInTheDocument();
  });

  it("asks the API for nothing only a signed-in caller may read", () => {
    renderAsGuest();
    const paths = mockApiGet.mock.calls.map(([path]) => String(path));
    expect(paths.filter((path) => path.startsWith("/api/me"))).toEqual([]);
  });
});

describe("AccountPage data export", () => {
  it("downloads the account's data from the API's origin", () => {
    renderAccount();
    expect(screen.getByRole("link", { name: "Download my data" })).toHaveAttribute("href", "https://api.example.test/api/me/export");
  });
});

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

  it("returns to the welcome page once signed out", async () => {
    const original = window.location;
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { ...original, assign }, writable: true });
    mockMutate.mockImplementation((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
    try {
      const user = userEvent.setup();
      renderAccount();
      await user.click(screen.getByRole("button", { name: "Sign out" }));
      expect(assign).toHaveBeenCalledWith("/welcome");
    } finally {
      Object.defineProperty(window, "location", { value: original, writable: true });
    }
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
      { sid_prefix: "abc123", user_agent: "Chrome", ip: "1.2.3.4", created_at: "2026-01-01T00:00:00Z", last_seen_at: "2026-01-02T03:04:00Z", current: false },
    ]);
    await i18n.changeLanguage("en");
    renderAccount();
    await screen.findByText("Unknown device");
    const expected = formatDateTime("2026-01-02T03:04:00Z");
    expect(document.body.textContent).toContain(expected);
  });
});

describe("AccountPage while the session loads", () => {
  it("holds the page's shape instead of a bare loading line", () => {
    mockSessionLoading = true;
    try {
      const { container } = renderAccount();
      expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
      expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(2);
    } finally {
      mockSessionLoading = false;
    }
  });
});
