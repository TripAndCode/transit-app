import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";
import { AdminAgenciesPage } from "../AdminAgenciesPage";

// Mock the admin API module
vi.mock("../../../api/admin", () => ({
  useAdminAgencies: () => ({
    data: [
      {
        agency_id: 1,
        agency_name: "Aomori Bus",
        feed_url: "http://feed.example.com",
        static_url: null,
        ingest_strategy: "aomori_regex",
        trip_id_pattern: null,
        deleted_at: null,
      },
      {
        agency_id: 2,
        agency_name: "Deleted Bus",
        feed_url: "http://del.example.com",
        static_url: null,
        ingest_strategy: null,
        trip_id_pattern: null,
        deleted_at: "2026-06-01T00:00:00Z",
      },
    ],
    isLoading: false,
    error: null,
  }),
  useCreateAgencyAdmin: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
  usePatchAgency: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteAgency: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useRestoreAgency: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminAgenciesPage", () => {
  it("renders active and deleted rows", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows Add agency button", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("button", { name: /add agency/i })).toBeTruthy();
  });

  it("opens form on Add click", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
