import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminAgenciesPage } from "./AdminAgenciesPage";

const createMutateAsync = vi.fn().mockResolvedValue({});
const patchMutateAsync = vi.fn().mockResolvedValue({});
const createReset = vi.fn();
const patchReset = vi.fn();
const delMutate = vi.fn();
const restoreMutate = vi.fn();
const probeMutate = vi.fn();
const reanalyzeMutate = vi.fn();

const HEALTH = [
  {
    agency_id: 1,
    agency_name: "Aomori Bus",
    feed_url: "http://feed.example.com",
    ingest_strategy: "aomori_regex",
    deleted_at: null,
    freshness: "fresh" as const,
    latest_data_date: "2026-09-19",
    last_analyzed_at: "2026-09-20T01:00:00+00:00",
    last_capture_at: "2026-09-20T02:30:00+00:00",
    rt_coverage: { complete: true, present_count: 4, field_count: 4, probed: true, last_probed_at: null },
    clamp_history: [{ date: "2026-09-19", clamp_pct: 0.4 }],
    static_version: { version: "v2026-09-01", loaded_at: null },
  },
  {
    agency_id: 2,
    agency_name: "Deleted Bus",
    feed_url: "http://del.example.com",
    ingest_strategy: null,
    deleted_at: "2026-06-01T00:00:00Z",
    freshness: "stale" as const,
    latest_data_date: "2026-09-01",
    last_analyzed_at: null,
    last_capture_at: null,
    rt_coverage: { complete: false, present_count: 0, field_count: 4, probed: false, last_probed_at: null },
    clamp_history: [{ date: "2026-09-19", clamp_pct: null }],
    static_version: null,
  },
];

const DIAGNOSTICS = {
  agency_id: 1,
  agency_name: "Aomori Bus",
  feed_url: "http://feed.example.com",
  static_url: null,
  ingest_strategy: "aomori_regex",
  deleted_at: null,
  freshness: "fresh" as const,
  last_analyzed_at: "2026-09-20T01:00:00+00:00",
  latest_data_date: "2026-09-19",
  last_capture_at: "2026-09-20T02:30:00+00:00",
  rt_coverage: { complete: true, last_probed_at: null, fields: {} },
  static_versions: [],
  clamp_history: [],
  weather_station: null,
  standards: [],
  standards_count: 0,
  weights: [],
  weights_coverage: { routes_with_weights: 0, routes_total: 0 },
};

let delState: { isPending: boolean; variables: number | undefined } = { isPending: false, variables: undefined };
let restoreState: { isPending: boolean; variables: number | undefined } = { isPending: false, variables: undefined };

// Mock the admin API module
vi.mock("../../api/admin", () => ({
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
  useCreateAgencyAdmin: () => ({ mutateAsync: createMutateAsync, isPending: false, error: null, reset: createReset }),
  usePatchAgency: () => ({ mutateAsync: patchMutateAsync, isPending: false, error: null, reset: patchReset }),
  useDeleteAgency: () => ({ mutate: delMutate, ...delState }),
  useRestoreAgency: () => ({ mutate: restoreMutate, ...restoreState }),
  useAgenciesHealth: () => ({ data: HEALTH, isLoading: false, error: null }),
  useAgencyDiagnostics: () => ({ data: DIAGNOSTICS, isLoading: false, error: null }),
  useProbeAgencyFeed: () => ({ mutate: probeMutate, isPending: false, error: null, data: undefined }),
  useReanalyzeAgency: () => ({ mutate: reanalyzeMutate, isPending: false, error: null, data: undefined }),
  usePatchAgencyStandards: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
  usePatchAgencyWeights: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
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
  beforeEach(() => {
    vi.clearAllMocks();
    createMutateAsync.mockResolvedValue({});
    patchMutateAsync.mockResolvedValue({});
    delState = { isPending: false, variables: undefined };
    restoreState = { isPending: false, variables: undefined };
  });

  it("renders active and deleted rows", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows Add agency button", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("button", { name: /add agency/i })).toBeTruthy();
  });

  it("opens form on Add click and resets stale mutation state", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(createReset).toHaveBeenCalled();
    expect(patchReset).toHaveBeenCalled();
  });

  it("resets stale mutation state when closing the form", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = screen.getByRole("dialog");
    createReset.mockClear();
    patchReset.mockClear();
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(createReset).toHaveBeenCalled();
    expect(patchReset).toHaveBeenCalled();
  });

  it("submits the add form and coalesces blank optional fields to null", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/agency name/i), "New Co");
    await user.type(within(dialog).getByLabelText(/feed url/i), "http://new.example.com");
    await user.click(within(dialog).getByRole("button", { name: /^add$/i }));
    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        agency_name: "New Co",
        feed_url: "http://new.example.com",
        static_url: null,
        ingest_strategy: null,
        trip_id_pattern: null,
      })
    );
  });

  it("renders the health columns from the health query", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByText("Fresh")).toBeTruthy();
    expect(screen.getByText("Behind")).toBeTruthy();
    expect(screen.getByText("4/4 fields")).toBeTruthy();
    expect(screen.getByText("Not probed")).toBeTruthy();
    expect(screen.getByText("v2026-09-01")).toBeTruthy();
  });

  it("opens the diagnostics drawer on a row click", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    expect(screen.getByLabelText("Agency details")).toBeTruthy();
  });

  it("disables an agency only after the typed confirm matches", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    const drawer = within(screen.getByLabelText("Agency details"));
    const confirmButton = drawer.getByRole("button", { name: /^Disable$/ });
    expect(confirmButton).toHaveProperty("disabled", true);
    await user.type(drawer.getByLabelText(/type the agency name to confirm/i), "Aomori Bus");
    await user.click(confirmButton);
    expect(delMutate).toHaveBeenCalledWith(1);
  });

  it("restores a disabled agency from its drawer, with no typed confirm", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Deleted Bus"));
    const drawer = within(screen.getByLabelText("Agency details"));
    expect(drawer.queryByLabelText(/type the agency name to confirm/i)).toBeNull();
    await user.click(drawer.getByRole("button", { name: /^Restore$/ }));
    expect(restoreMutate).toHaveBeenCalledWith(2);
  });

  it("narrows the list to behind-schedule agencies via the saved view", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /Behind schedule/ }));
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("filters rows by agency name via the search input", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "Deleted");
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows an empty-state row when the search matches nothing", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "nonexistent-agency");
    expect(screen.getByText("No agencies found.")).toBeTruthy();
  });
});
