import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import * as client from "../api/client";
import { AccountPage } from "./AccountPage";

void i18n.changeLanguage("en");

const mockSession = { user_id: 1, email: "yo@example.com", name: "Yo", avatar_url: null, role: "user" as const, identities: [] };
vi.mock("../api/auth", () => ({
  useSession: () => ({ data: mockSession, isLoading: false }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

afterEach(() => vi.restoreAllMocks());

function renderPage() {
  vi.spyOn(client, "apiGet").mockImplementation(async (path: string) =>
    path === "/api/me/llm-key" ? { configured: false } : [],
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/me"]}>
          <Routes>
            <Route path="/me" element={<AccountPage />} />
            <Route path="/welcome" element={<div>landed:welcome</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

async function openDeleteDialog() {
  await userEvent.click(screen.getByRole("button", { name: "Delete account" }));
  return screen.getByRole("dialog");
}

describe("AccountPage — your data", () => {
  it("offers the export as a download link", () => {
    renderPage();
    const link = screen.getByRole("link", { name: "Download my data" });
    expect(link).toHaveAttribute("href", "/api/me/export");
    expect(link).toHaveAttribute("download");
  });

  it("enables deletion only once the typed email matches, ignoring case", async () => {
    renderPage();
    await openDeleteDialog();
    const confirm = screen.getByRole("button", { name: "Delete permanently" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "YO@example.com");
    expect(confirm).toBeEnabled();
  });

  it("deletes with the typed confirmation and lands on the welcome page", async () => {
    const del = vi.spyOn(client, "apiDelete").mockResolvedValue(undefined);
    renderPage();
    await openDeleteDialog();
    await userEvent.type(screen.getByRole("textbox"), "yo@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(screen.getByText("landed:welcome")).toBeTruthy());
    expect(del).toHaveBeenCalledWith("/api/me", { body: { confirm_email: "yo@example.com" } });
  });

  it("disables the confirm button while the deletion is in flight", async () => {
    vi.spyOn(client, "apiDelete").mockReturnValue(new Promise(() => {}));
    renderPage();
    await openDeleteDialog();
    await userEvent.type(screen.getByRole("textbox"), "yo@example.com");
    const confirm = screen.getByRole("button", { name: "Delete permanently" });
    await userEvent.click(confirm);
    expect(confirm).toBeDisabled();
  });

  it("explains why the last admin cannot delete their account", async () => {
    vi.spyOn(client, "apiDelete").mockRejectedValue(new client.ApiError(409, JSON.stringify({ detail: "last_admin" })));
    renderPage();
    await openDeleteDialog();
    await userEvent.type(screen.getByRole("textbox"), "yo@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(await screen.findByText(/only admin/i)).toBeTruthy();
  });
});
