import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import * as client from "../api/client";
import { AccountPage } from "./AccountPage";

void i18n.changeLanguage("en");

const mockSession = { user_id: 1, email: "yo@example.com", name: "Yo", avatar_url: null, role: "user" as const, identities: [] };
vi.mock("../api/auth", () => ({
  useSession: () => ({ data: mockSession, isLoading: false }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

const SESSIONS = [
  {
    sid_prefix: "aaaaaaaaaaaa",
    user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    ip: null,
    created_at: "2026-10-01T00:00:00Z",
    last_seen_at: "2026-10-04T13:31:00Z",
    current: true,
  },
  {
    sid_prefix: "bbbbbbbbbbbb",
    user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
    ip: null,
    created_at: "2026-09-20T00:00:00Z",
    last_seen_at: "2026-09-30T08:00:00Z",
    current: false,
  },
];

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

function renderPage() {
  vi.spyOn(client, "apiGet").mockImplementation(async (path: string) => {
    if (path === "/api/me/llm-key") return { configured: false };
    if (path === "/api/me/sessions") return SESSIONS;
    if (path === "/api/agencies") return [{ agency_id: 8, agency_name: "Aomori City Bus", feed_url: "", static_url: null, latest_data_date: null }];
    return [];
  });
  const remove = vi.spyOn(client, "apiDelete").mockResolvedValue(undefined as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/me"]}>
          <Routes>
            <Route path="/me" element={<AccountPage />} />
            <Route path="/agencies/:agencyId/pulse" element={<div>landed:pulse</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return { remove };
}

describe("AccountPage context", () => {
  it("offers a way back to the agency last used", async () => {
    localStorage.setItem("transit.lastAgency", "8");
    renderPage();
    await userEvent.click(await screen.findByRole("link", { name: "← Back to Aomori City Bus" }));
    expect(screen.getByText("landed:pulse")).toBeInTheDocument();
  });

  it("says when no sign-in provider is linked, instead of an empty heading", () => {
    renderPage();
    expect(screen.getByText("None linked")).toBeInTheDocument();
  });

  it("names each session's device and marks this one", async () => {
    renderPage();
    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByText("Safari on iOS")).toBeInTheDocument();
    expect(screen.getByText("This device")).toBeInTheDocument();
  });

  it("signs out another session from its own row", async () => {
    const { remove } = renderPage();
    await screen.findByText("Safari on iOS");
    const signOuts = screen.getAllByRole("button", { name: "Sign out this session" });
    expect(signOuts).toHaveLength(1);
    await userEvent.click(signOuts[0]);
    expect(remove).toHaveBeenCalledWith("/api/me/sessions/bbbbbbbbbbbb");
  });

  it("keeps account deletion apart from the data download, in its own warning section", () => {
    renderPage();
    const exportSection = screen.getByRole("link", { name: "Download my data" }).closest("section");
    const deleteButton = screen.getByRole("button", { name: "Delete account" });
    expect(exportSection).not.toContainElement(deleteButton);
    expect(within(deleteButton.closest("section")!).getByRole("heading", { name: "Delete account" })).toBeInTheDocument();
  });
});
