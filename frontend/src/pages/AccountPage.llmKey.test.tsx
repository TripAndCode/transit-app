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
      .mockResolvedValue({ configured: true, provider: "groq", key_suffix: "ab12" });
    renderPage();
    await userEvent.type(
      await screen.findByLabelText(/api key|apiキー/i),
      "gsk_realsecretvalueab12",
    );
    await userEvent.click(screen.getByText(/^save$|^保存$/i));
    await waitFor(() => expect(putSpy).toHaveBeenCalled());
    expect(await screen.findByText(/ab12/)).toBeTruthy();
    expect(screen.queryByText("gsk_realsecretvalueab12")).toBeNull();
  });
});
