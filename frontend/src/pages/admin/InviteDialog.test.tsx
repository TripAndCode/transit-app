import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { InviteDialog } from "./InviteDialog";

const apiPostMock = vi.hoisted(() => vi.fn());

vi.mock("../../api/client", () => ({
  apiGet: vi.fn(),
  apiPost: apiPostMock,
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  formatApiError: () => "error",
}));

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <InviteDialog open onClose={onClose} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return onClose;
}

beforeEach(() => {
  apiPostMock.mockReset();
});

describe("InviteDialog", () => {
  it("renders nothing when closed", () => {
    const qc = new QueryClient();
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={qc}>
          <InviteDialog open={false} onClose={vi.fn()} />
        </QueryClientProvider>
      </I18nextProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("submits email/role/llm_approved to POST /api/admin/invites", async () => {
    apiPostMock.mockResolvedValue({
      invite_id: 1,
      email: "new@example.com",
      role: "admin",
      llm_approved: true,
      created_at: "2026-01-01T00:00:00Z",
      expires_at: "2026-01-15T00:00:00Z",
    });
    const user = userEvent.setup();
    renderDialog();
    await user.type(screen.getByRole("textbox"), "new@example.com");
    await user.selectOptions(screen.getByLabelText(/role/i), "admin");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /send invite/i }));
    expect(apiPostMock).toHaveBeenCalledWith("/api/admin/invites", {
      email: "new@example.com",
      role: "admin",
      llm_approved: true,
    });
    expect(await screen.findByText(/new@example.com/)).toBeTruthy();
  });

  it("calls onClose when cancel is clicked", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
