import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { ApiKeysSection, SessionsSection } from "./UserDrawerSections";

const apiGetMock = vi.hoisted(() => vi.fn());
const apiPostMock = vi.hoisted(() => vi.fn());
const apiDeleteMock = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiDelete: apiDeleteMock,
  apiPatch: vi.fn(),
  formatApiError: () => "error",
}));

function renderWithProviders(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>{node}</QueryClientProvider>
    </I18nextProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiDeleteMock.mockClear();
});

describe("ApiKeysSection", () => {
  it("shows the raw key exactly once, in a copy box, right after issuing", async () => {
    apiGetMock.mockResolvedValue([]);
    apiPostMock.mockResolvedValue({
      id: 1,
      owner_user_id: 7,
      tier: "pro",
      label: null,
      created_at: "2026-01-01T00:00:00Z",
      expires_at: null,
      revoked_at: null,
      key: "sk_raw_secret_value",
    });
    const user = userEvent.setup();
    renderWithProviders(<ApiKeysSection uid={7} />);
    await user.click(await screen.findByRole("button", { name: /issue/i }));
    expect(await screen.findByDisplayValue("sk_raw_secret_value")).toBeTruthy();
    expect(apiPostMock).toHaveBeenCalledWith("/api/admin/api-keys", { owner_user_id: 7 });
  });

  it("never renders a raw key from the list endpoint (only from the issue response)", async () => {
    apiGetMock.mockResolvedValue([
      { id: 1, owner_user_id: 7, tier: "pro", label: "svc", created_at: "2026-01-01T00:00:00Z", expires_at: null, revoked_at: null },
    ]);
    renderWithProviders(<ApiKeysSection uid={7} />);
    await screen.findByText("svc");
    expect(screen.queryByDisplayValue(/sk_/)).toBeNull();
  });

  it("revokes a key via the revoke button", async () => {
    apiGetMock.mockResolvedValue([
      { id: 9, owner_user_id: 7, tier: "pro", label: "svc", created_at: "2026-01-01T00:00:00Z", expires_at: null, revoked_at: null },
    ]);
    const user = userEvent.setup();
    renderWithProviders(<ApiKeysSection uid={7} />);
    await user.click(await screen.findByRole("button", { name: /revoke/i }));
    expect(apiDeleteMock).toHaveBeenCalledWith("/api/admin/api-keys/9");
  });
});

describe("SessionsSection", () => {
  it("lists sessions by prefix and revokes one", async () => {
    apiGetMock.mockResolvedValue([
      {
        sid_prefix: "abcdef123456",
        created_at: "2026-01-01T00:00:00Z",
        last_seen_at: "2026-01-02T00:00:00Z",
        expires_at: "2026-02-01T00:00:00Z",
        user_agent: null,
        ip: null,
      },
    ]);
    const user = userEvent.setup();
    renderWithProviders(<SessionsSection uid={7} />);
    await screen.findByText(/abcdef123456/);
    await user.click(screen.getByRole("button", { name: /revoke/i }));
    expect(apiDeleteMock).toHaveBeenCalledWith("/api/admin/users/7/sessions/abcdef123456");
  });

  it("shows an empty state with no sessions", async () => {
    apiGetMock.mockResolvedValue([]);
    renderWithProviders(<SessionsSection uid={7} />);
    expect(await screen.findByText(/no active sessions|セッション/i)).toBeTruthy();
  });
});
