import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountPage } from "./AccountPage";
import * as client from "../api/client";

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
  useLogout: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

// Matches the rest of the suite's convention (e.g. CopilotPanel.test.tsx): a
// fresh spy per test so call counts/histories don't leak across tests.
afterEach(() => vi.restoreAllMocks());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AccountPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AccountPage BYOK section", () => {
  it("shows the shared-tier status when no key is configured", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: false } : [],
    );
    renderPage();
    // Bilingual match — jsdom's detected language isn't pinned here (matches
    // the ErrorBanner.test.tsx / CopilotPanel.test.tsx convention for
    // un-pinned-locale assertions).
    expect(
      await screen.findByText(/using the shared free tier|無料の共有枠を使用中/i),
    ).toBeTruthy();
  });

  it("never renders the full key after saving, only the masked suffix", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: false } : [],
    );
    const putSpy = vi
      .spyOn(client, "apiPut")
      .mockResolvedValue({ configured: true, provider: "gemini", key_suffix: "ab12" });
    renderPage();
    await userEvent.type(
      await screen.findByLabelText(/api key|apiキー/i),
      "AQ.realsecretvalueab12",
    );
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    await waitFor(() => expect(putSpy).toHaveBeenCalled());
    expect(await screen.findByText(/ab12/)).toBeTruthy();
    expect(screen.queryByText("AQ.realsecretvalueab12")).toBeNull();
  });

  it("defaults the provider selector to the already-configured provider, not gemini", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: true, provider: "openai", key_suffix: "cd34" } : [],
    );
    const putSpy = vi
      .spyOn(client, "apiPut")
      .mockResolvedValue({ configured: true, provider: "openai", key_suffix: "ef56" });
    renderPage();
    await screen.findByText(/cd34/);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("openai");
    await userEvent.type(
      await screen.findByLabelText(/api key|apiキー/i),
      "sk-newkeyvalue",
    );
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    await waitFor(() => expect(putSpy).toHaveBeenCalled());
    expect(putSpy).toHaveBeenCalledWith("/api/me/llm-key", { provider: "openai", api_key: "sk-newkeyvalue" });
  });

  it("does not call the key status 'shared tier' when its fetch failed, and keeps Remove reachable", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) => {
      if (path === "/api/me/llm-key") throw new client.ApiError(503, "down");
      return [];
    });
    renderPage();
    expect(await screen.findByText(/couldn't load your key status|キーの状態を読み込めませんでした/i)).toBeTruthy();
    expect(screen.queryByText(/using the shared free tier|無料の共有枠を使用中/i)).toBeNull();
    expect(screen.getByText(/^remove$|^削除$/i)).toBeTruthy();
  });

  it("does not call the key status 'shared tier' while its fetch is still pending", async () => {
    vi.spyOn(client, "apiGet").mockImplementation((path: string) =>
      path === "/api/me/llm-key" ? new Promise(() => {}) : Promise.resolve([]),
    );
    renderPage();
    await screen.findByLabelText(/api key|apiキー/i);
    expect(screen.queryByText(/using the shared free tier|無料の共有枠を使用中/i)).toBeNull();
  });

  it("says 'rejected' only for a 400 from key validation", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: false } : [],
    );
    vi.spyOn(client, "apiPut").mockRejectedValue(new client.ApiError(400, JSON.stringify({ detail: "key_rejected" })));
    renderPage();
    await userEvent.type(await screen.findByLabelText(/api key|apiキー/i), "bad");
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    expect(await screen.findByRole("alert")).toHaveTextContent(/key rejected|キーが拒否/i);
  });

  it("does not call a 5xx or network failure on save a rejected key", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: false } : [],
    );
    vi.spyOn(client, "apiPut").mockRejectedValue(new client.ApiError(503, JSON.stringify({ detail: "validation_unavailable" })));
    renderPage();
    await userEvent.type(await screen.findByLabelText(/api key|apiキー/i), "maybe-good");
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    const alert = await screen.findByRole("alert");
    expect(alert).not.toHaveTextContent(/key rejected|キーが拒否/i);
    expect(alert).toHaveTextContent(/couldn't save|保存できませんでした/i);
  });

  it("does not call an expired session or CSRF refusal (403) on save a rejected key", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
      path === "/api/me/llm-key" ? { configured: false } : [],
    );
    vi.spyOn(client, "apiPut").mockRejectedValue(new client.ApiError(403, JSON.stringify({ detail: "csrf" })));
    renderPage();
    await userEvent.type(await screen.findByLabelText(/api key|apiキー/i), "maybe-good");
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    const alert = await screen.findByRole("alert");
    expect(alert).not.toHaveTextContent(/key rejected|キーが拒否/i);
    expect(alert).toHaveTextContent(/couldn't save|保存できませんでした/i);
  });

  it("shows an error, not a blank section, when the sessions fetch failed", async () => {
    vi.spyOn(client, "apiGet").mockImplementation(async (path: string) => {
      if (path === "/api/me/sessions") throw new client.ApiError(500, "boom");
      return path === "/api/me/llm-key" ? { configured: false } : [];
    });
    renderPage();
    expect(await screen.findByText(/couldn't load your sessions|セッションを読み込めませんでした/i)).toBeTruthy();
  });
});
