import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminOpsPage } from "./AdminOpsPage";
import type { AgencyFreshnessItem } from "../../api/admin";

type OpsHealthData = {
  migrations: { applied: string | null; latest: string | null; behind: number } | null;
  agencies: AgencyFreshnessItem[];
  agencies_ok: boolean;
  last_deploy: { deployed_at: string; git_sha: string | null; migrations_applied: string[] } | null;
  deploys_ok: boolean;
};
type OpsHealthResult = { data: OpsHealthData | undefined; isLoading: boolean; error: Error | null };

// Mock state to control what useAdminOps returns
let mockReturnValue: OpsHealthResult = {
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
    agencies_ok: true,
    last_deploy: null,
    deploys_ok: true,
  },
  isLoading: false,
  error: null,
};

vi.mock("../../api/admin", () => ({
  useAdminOps: () => mockReturnValue,
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
  beforeEach(() => {
    // Reset to default return value
    mockReturnValue = {
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
        agencies_ok: true,
        last_deploy: null,
        deploys_ok: true,
      },
      isLoading: false,
      error: null,
    };
  });

  it("renders migration status as up-to-date", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText(/schema up to date/i)).toBeTruthy();
  });

  it("points the cross-agency comparison at Compare by agencies", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText("To compare delay across agencies, open Day types & agencies and choose Agencies.")).toBeTruthy();
  });

  it("renders agency row", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
  });

  it("shows migration status as unknown (not a false OK) when migrations is null", () => {
    // Backend degrades a failed migration_status() check to null, still 200.
    mockReturnValue = {
      data: { migrations: null, agencies: [], agencies_ok: true, last_deploy: null, deploys_ok: true },
      isLoading: false,
      error: null,
    };

    wrap(<AdminOpsPage />);
    expect(screen.getByText(/migration status unavailable/i)).toBeTruthy();
    expect(screen.queryByText(/schema up to date/i)).toBeNull();
  });

  it("shows agency freshness as unknown (not a false all-fresh) when agencies_ok is false", () => {
    // Backend degrades a failed aggregate_freshness() check to [] + agencies_ok=false.
    mockReturnValue = {
      data: {
        migrations: { applied: "0026", latest: "0026", behind: 0 },
        agencies: [],
        agencies_ok: false,
        last_deploy: null,
        deploys_ok: true,
      },
      isLoading: false,
      error: null,
    };

    wrap(<AdminOpsPage />);
    expect(screen.getByText(/agency freshness unavailable/i)).toBeTruthy();
    expect(screen.queryByText(/all agencies fresh/i)).toBeNull();
  });

  it("shows a load-error banner and unknown status on a full request failure", () => {
    mockReturnValue = {
      data: undefined,
      isLoading: false,
      error: new Error("network down"),
    };

    wrap(<AdminOpsPage />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/migration status unavailable/i)).toBeTruthy();
    expect(screen.getByText(/agency freshness unavailable/i)).toBeTruthy();
    expect(screen.getByText("Deploy record unavailable")).toBeTruthy();
  });

  it("says no deploy is recorded yet when the lookup succeeded and found none", () => {
    wrap(<AdminOpsPage />);
    expect(screen.getByText("No deploy recorded yet")).toBeTruthy();
    expect(screen.queryByText("Deploy record unavailable")).toBeNull();
  });

  it("says the deploy record is unavailable, not that none exists, when the lookup failed", () => {
    mockReturnValue = { ...mockReturnValue, data: { ...mockReturnValue.data!, deploys_ok: false } };

    wrap(<AdminOpsPage />);
    expect(screen.getByText("Deploy record unavailable")).toBeTruthy();
    expect(screen.queryByText("No deploy recorded yet")).toBeNull();
  });

  it("links the last deploy's commit to what main has gained since", () => {
    const sha = "bbbbbbb0123456789abcdef0123456789abcdef";
    mockReturnValue = {
      ...mockReturnValue,
      data: {
        ...mockReturnValue.data!,
        last_deploy: { deployed_at: "2026-11-15T00:00:00+00:00", git_sha: sha, migrations_applied: ["0114", "0115"] },
      },
    };

    wrap(<AdminOpsPage />);
    const link = screen.getByRole("link", { name: "commit bbbbbbb: compare with main" });
    expect(link.getAttribute("href")).toBe(`https://github.com/TripAndCode/transit-app/compare/${sha}...main`);
    expect(screen.getByText(/2 migrations applied/)).toBeTruthy();
    expect(screen.getByText(/Last deploy/)).toBeTruthy();
  });

  it("names no commit and links nowhere when the release was recorded without one", () => {
    for (const git_sha of [null, "main...evil"]) {
      mockReturnValue = {
        ...mockReturnValue,
        data: {
          ...mockReturnValue.data!,
          last_deploy: { deployed_at: "2026-11-15T00:00:00+00:00", git_sha, migrations_applied: ["0114"] },
        },
      };

      const { unmount } = wrap(<AdminOpsPage />);
      expect(screen.getByText(/commit unknown/)).toBeTruthy();
      expect(screen.getByText(/1 migration applied/)).toBeTruthy();
      expect(screen.queryByRole("link")).toBeNull();
      unmount();
    }
  });

  it("shows loading text while the query is in flight", () => {
    mockReturnValue = { data: undefined, isLoading: true, error: null };

    wrap(<AdminOpsPage />);
    expect(screen.getAllByText(/loading/i).length).toBeGreaterThan(0);
  });
});
