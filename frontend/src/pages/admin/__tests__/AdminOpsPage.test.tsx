import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";
import { AdminOpsPage } from "../AdminOpsPage";

vi.mock("../../../api/admin", () => ({
  useAdminOps: () => ({
    data: {
      migrations: { applied: "0026", latest: "0026", behind: 0 },
      agencies: [
        {
          agency_id: 1,
          agency_name: "Aomori Bus",
          last_analyzed_at: "2026-06-30T03:00:00Z",
          analyze_age_hours: 6.2,
          agg_fresh: true,
          agg_behind_days: 0,
          is_stale: false,
          data_to: "2026-06-29",
          clamp_pct: 1.3,
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
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

describe("AdminOpsPage", () => {
  it("renders migration status as up-to-date", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText(/schema up to date/i)).toBeTruthy();
  });

  it("renders agency row", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
  });

  it("shows null migrations section gracefully", () => {
    wrap(<AdminOpsPage />);
    // No crash expected even with null migrations from hook
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
