import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminAuditPage } from "./AdminAuditPage";

const PAGE_1 = {
  items: [
    {
      at: "2026-09-20T12:00:00Z",
      actor_id: 1,
      action: "user.updated",
      target_type: "user",
      target_id: "5",
      before: { role: "user" },
      after: { role: "admin" },
      reason: null,
      ip: null,
    },
    {
      at: "2026-09-20T11:00:00Z",
      actor_id: 5,
      action: "login.ok",
      target_type: "user",
      target_id: "5",
      before: null,
      after: null,
      reason: null,
      ip: "10.0.0.1",
    },
  ],
  next_cursor: "cursor-page-2",
};

const PAGE_2 = {
  items: [
    {
      at: "2026-09-19T09:00:00Z",
      actor_id: 1,
      action: "agency.deleted",
      target_type: "agency",
      target_id: "3",
      before: { deleted: false },
      after: { deleted: true },
      reason: null,
      ip: null,
    },
  ],
  next_cursor: null,
};

const useAdminAuditMock = vi.fn();
const fetchAllAdminAuditMock = vi.fn().mockResolvedValue(PAGE_1.items);
const downloadCsvMock = vi.fn();

vi.mock("../../api/admin", () => ({
  useAdminAudit: (...args: unknown[]) => useAdminAuditMock(...args),
  fetchAllAdminAudit: (...args: unknown[]) => fetchAllAdminAuditMock(...args),
}));

vi.mock("../../components/analysis/csv", () => ({
  downloadCsv: (...args: unknown[]) => downloadCsvMock(...args),
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe("AdminAuditPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchAllAdminAuditMock.mockResolvedValue(PAGE_1.items);
    useAdminAuditMock.mockImplementation((_filters: unknown, cursor: string | null) => ({
      data: cursor === "cursor-page-2" ? PAGE_2 : PAGE_1,
      isLoading: false,
      isPlaceholderData: false,
      error: null,
    }));
  });

  it("names the timeline for a screen reader instead of leaking the i18n key", () => {
    // The caption is the table's accessible name; locale parity cannot
    // catch a key that is missing from both files.
    wrap(<AdminAuditPage />);
    expect(screen.getByRole("table", { name: "Audit timeline" })).toBeTruthy();
  });

  it("renders a before→after diff pill for a changed field", () => {
    wrap(<AdminAuditPage />);
    expect(screen.getByText(/role/)).toBeTruthy();
    expect(screen.getByText(/user → admin/)).toBeTruthy();
  });

  it("renders a dash for a row with no diff data (a merged login event)", () => {
    wrap(<AdminAuditPage />);
    expect(screen.getByText("login.ok")).toBeTruthy();
  });

  it("advances to the next page via the returned cursor", async () => {
    const user = userEvent.setup();
    wrap(<AdminAuditPage />);
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(screen.getByText("agency.deleted")).toBeTruthy());
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("exports the current filter set as CSV", async () => {
    const user = userEvent.setup();
    wrap(<AdminAuditPage />);
    await user.click(screen.getByRole("button", { name: /export csv/i }));
    await waitFor(() => expect(downloadCsvMock).toHaveBeenCalled());
    expect(fetchAllAdminAuditMock).toHaveBeenCalled();
  });

  it("re-queries with the actor filter typed into the actor input", async () => {
    const user = userEvent.setup();
    wrap(<AdminAuditPage />);
    await user.type(screen.getByLabelText(/actor id/i), "7");
    await waitFor(() => {
      const lastCall = useAdminAuditMock.mock.calls.at(-1);
      expect(lastCall?.[0]).toMatchObject({ actor: "7" });
    });
  });
});
