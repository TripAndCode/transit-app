import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminAgenciesPage } from "./AdminAgenciesPage";
import type { AdminAgency, AgencyRun } from "../../api/admin";

const createMutateAsync = vi.fn().mockResolvedValue({});
const patchMutateAsync = vi.fn().mockResolvedValue({});
const createReset = vi.fn();
const patchReset = vi.fn();
const delMutate = vi.fn();
const restoreMutate = vi.fn();
const delReset = vi.fn();
const restoreReset = vi.fn();
const probeMutate = vi.fn();
const reanalyzeMutate = vi.fn();
const updateMutate = vi.fn();
const refreshMutate = vi.fn();

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
    static_version: { version: "v2026-09-01", analyzed_at: null },
    static_loaded_on: "2026-09-18",
    in_flight: false,
    latest_run: null as AgencyRun | null,
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
    static_loaded_on: null,
    in_flight: false,
    latest_run: null as AgencyRun | null,
  },
];

const RUNNING_UPDATE: AgencyRun = {
  run_id: 7,
  kind: "update",
  status: "running",
  started_at: "2026-10-13T05:00:00Z",
  finished_at: null,
  error: null,
  steps: [
    { step: "static", status: "ok", started_at: "2026-10-13T05:00:00Z", finished_at: "2026-10-13T05:00:48Z", rows: 27, error: null },
    { step: "ingest", status: "running", started_at: "2026-10-13T05:00:48Z", finished_at: null, rows: null, error: null },
    { step: "analyze", status: "pending", started_at: null, finished_at: null, rows: null, error: null },
  ],
};

/** Replaces the health fixture's rows for one test; `null` keeps it. */
let healthOverride: typeof HEALTH | null = null;

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

/** Overrides fields of the agencies list query result; `null` keeps the fixture. */
let agenciesOverride: { data?: AdminAgency[]; isLoading?: boolean; error?: Error | null } | null = null;

let delState: { isPending: boolean; variables: number | undefined; error?: Error } = {
  isPending: false,
  variables: undefined,
};
let restoreState: { isPending: boolean; variables: number | undefined; error?: Error } = {
  isPending: false,
  variables: undefined,
};

// Mock the admin API module
vi.mock("../../api/admin", () => ({
  useAdminAgencies: () => ({
    data: [
      {
        agency_id: 1,
        agency_name: "Aomori Bus",
        feed_url: "http://feed.example.com",
        static_url: "http://feed.example.com/gtfs.zip",
        static_strategy: "aomori_index_scrape",
        ingest_strategy: "aomori_regex",
        trip_id_pattern: null,
        deleted_at: null,
      },
      {
        agency_id: 2,
        agency_name: "Deleted Bus",
        feed_url: "http://del.example.com",
        static_url: null,
        static_strategy: null,
        ingest_strategy: null,
        trip_id_pattern: null,
        deleted_at: "2026-06-01T00:00:00Z",
      },
    ],
    isLoading: false,
    error: null,
    ...agenciesOverride,
  }),
  useCreateAgencyAdmin: () => ({ mutateAsync: createMutateAsync, isPending: false, error: null, reset: createReset }),
  usePatchAgency: () => ({ mutateAsync: patchMutateAsync, isPending: false, error: null, reset: patchReset }),
  useDeleteAgency: () => ({ mutate: delMutate, reset: delReset, error: null, ...delState }),
  useRestoreAgency: () => ({ mutate: restoreMutate, reset: restoreReset, error: null, ...restoreState }),
  useAgenciesHealth: () => ({ data: healthOverride ?? HEALTH, isLoading: false, error: null }),
  useAgencyDiagnostics: () => ({ data: DIAGNOSTICS, isLoading: false, error: null }),
  useProbeAgencyFeed: () => ({ mutate: probeMutate, isPending: false, error: null, data: undefined }),
  useReanalyzeAgency: () => ({ mutate: reanalyzeMutate, isPending: false, error: null, data: undefined }),
  useUpdateAgency: () => ({ mutate: updateMutate, isPending: false, error: null, variables: undefined }),
  useRefreshAgencyStatic: () => ({ mutate: refreshMutate, isPending: false, error: null, variables: undefined }),
  usePatchAgencyStandards: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
  usePatchAgencyWeights: () => ({ mutateAsync: vi.fn(), isPending: false, error: null }),
}));

function wrap(ui: React.ReactElement, initialEntries = ["/admin/agencies"]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminAgenciesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    agenciesOverride = null;
    healthOverride = null;
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

  it("disables Edit on a disabled agency, whose PATCH would 404, and keeps it on an active one", () => {
    wrap(<AdminAgenciesPage />);
    const editFor = (name: string) => {
      const row = screen.getByText(name).closest("tr") as HTMLElement;
      return within(row).getByRole("button", { name: "Edit" }) as HTMLButtonElement;
    };
    expect(editFor("Aomori Bus").disabled).toBe(false);
    const deletedEdit = editFor("Deleted Bus");
    expect(deletedEdit.disabled).toBe(true);
    expect(deletedEdit.title).toMatch(/restore/i);
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
    await user.click(within(dialog).getByRole("button", { name: /^save only$/i }));
    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        agency_name: "New Co",
        feed_url: "http://new.example.com",
        static_url: null,
        static_strategy: null,
        ingest_strategy: null,
        trip_id_pattern: null,
      })
    );
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("offers set-up only once the new agency has a timetable source", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = within(screen.getByRole("dialog"));
    const setup = dialog.getByRole("button", { name: "Save and set up" }) as HTMLButtonElement;
    expect(setup.disabled).toBe(true);
    await user.type(dialog.getByLabelText(/GTFS static URL/i), "http://new.example.com/gtfs.zip");
    expect((dialog.getByLabelText(/timetable fetch method/i) as HTMLSelectElement).value).toBe("direct_url");
    expect(setup.disabled).toBe(false);
  });

  it("saves the new agency, then starts its chained update", async () => {
    const user = userEvent.setup();
    createMutateAsync.mockResolvedValue({ agency_id: 31 });
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText(/agency name/i), "New Co");
    await user.type(dialog.getByLabelText(/feed url/i), "http://new.example.com");
    await user.type(dialog.getByLabelText(/GTFS static URL/i), "http://new.example.com/gtfs.zip");
    await user.click(dialog.getByRole("button", { name: "Save and set up" }));
    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ static_url: "http://new.example.com/gtfs.zip", static_strategy: "direct_url" })
    );
    expect(updateMutate).toHaveBeenCalledWith(31);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not save through set-up while a required field is empty", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText(/GTFS static URL/i), "http://new.example.com/gtfs.zip");
    await user.click(dialog.getByRole("button", { name: "Save and set up" }));
    expect(createMutateAsync).not.toHaveBeenCalled();
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("keeps a single update button in the edit form", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    const row = screen.getByText("Aomori Bus").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.queryByRole("button", { name: "Save and set up" })).toBeNull();
    expect(dialog.getByRole("button", { name: "Update" })).toBeTruthy();
    expect((dialog.getByLabelText(/timetable fetch method/i) as HTMLSelectElement).value).toBe("aomori_index_scrape");
  });

  it("renders the health columns from the health query", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByText("Fresh")).toBeTruthy();
    expect(screen.getByText("Behind")).toBeTruthy();
    expect(screen.getByText("4/4 fields")).toBeTruthy();
    expect(screen.getByText("Not probed")).toBeTruthy();
  });

  it("shows each agency's timetable and data stages with their own buttons", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    const row = within(screen.getByText("Aomori Bus").closest("tr") as HTMLElement);
    expect(row.getByText(/^Loaded /)).toBeTruthy();
    expect(row.getByText(/^Through /)).toBeTruthy();
    await user.click(row.getByRole("button", { name: "Reload" }));
    await user.click(row.getByRole("button", { name: "Re-aggregate" }));
    await user.click(row.getByRole("button", { name: "Update all" }));
    expect(refreshMutate).toHaveBeenCalledWith(1);
    expect(reanalyzeMutate).toHaveBeenCalledWith(1);
    expect(updateMutate).toHaveBeenCalledWith(1);
    // The buttons sit inside the row, whose click opens the drawer.
    expect(screen.queryByLabelText("Agency details")).toBeNull();
  });

  it("disables every run button of a disabled agency", () => {
    wrap(<AdminAgenciesPage />);
    const deleted = within(screen.getByText("Deleted Bus").closest("tr") as HTMLElement);
    for (const name of ["Reload", "Re-aggregate", "Update all"]) {
      expect((deleted.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("keeps re-aggregation but not the timetable runs for an agency with no timetable source", () => {
    agenciesOverride = {
      data: [
        {
          agency_id: 1,
          agency_name: "Aomori Bus",
          feed_url: "http://feed.example.com",
          static_url: null,
          static_strategy: null,
          ingest_strategy: "aomori_regex",
          trip_id_pattern: null,
          deleted_at: null,
        },
      ],
    };
    wrap(<AdminAgenciesPage />);
    const row = within(screen.getByText("Aomori Bus").closest("tr") as HTMLElement);
    const reload = row.getByRole("button", { name: "Reload" }) as HTMLButtonElement;
    expect(reload.disabled).toBe(true);
    expect(reload.title).toMatch(/timetable URL/);
    expect((row.getByRole("button", { name: "Update all" }) as HTMLButtonElement).disabled).toBe(true);
    expect((row.getByRole("button", { name: "Re-aggregate" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("replaces a running agency's stage with its step chain and shows Running on its button", () => {
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, in_flight: true, latest_run: RUNNING_UPDATE } : h));
    wrap(<AdminAgenciesPage />);
    const row = within(screen.getByText("Aomori Bus").closest("tr") as HTMLElement);
    expect(row.getByRole("list", { name: "Update in progress" })).toBeTruthy();
    expect(row.queryByRole("button", { name: "Reload" })).toBeNull();
    const running = row.getByRole("button", { name: "Running" }) as HTMLButtonElement;
    expect(running.disabled).toBe(true);
    expect((row.getByRole("button", { name: "Re-aggregate" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("frees a row whose run row was left running past the point the server still counts it", () => {
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, in_flight: false, latest_run: RUNNING_UPDATE } : h));
    wrap(<AdminAgenciesPage />);
    const row = within(screen.getByText("Aomori Bus").closest("tr") as HTMLElement);
    expect(row.queryByRole("list", { name: "Update in progress" })).toBeNull();
    expect((row.getByRole("button", { name: "Update all" }) as HTMLButtonElement).disabled).toBe(false);
    expect((row.getByRole("button", { name: "Reload" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows Running without a chain while another job, such as a cron sweep, holds the agency", () => {
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, in_flight: true } : h));
    wrap(<AdminAgenciesPage />);
    const row = within(screen.getByText("Aomori Bus").closest("tr") as HTMLElement);
    expect(row.queryByRole("list", { name: "Update in progress" })).toBeNull();
    expect((row.getByRole("button", { name: "Running" }) as HTMLButtonElement).disabled).toBe(true);
    expect((row.getByRole("button", { name: "Reload" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens the agency's latest run at the top of its drawer", async () => {
    const user = userEvent.setup();
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, in_flight: true, latest_run: RUNNING_UPDATE } : h));
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    const drawer = within(screen.getByLabelText("Agency details"));
    expect(drawer.getByRole("region", { name: "Latest run" })).toBeTruthy();
    expect(drawer.getByRole("img", { name: "Step 2 of 3 running" })).toBeTruthy();
  });

  it("offers a retry for a run whose process died, once the server no longer counts it", async () => {
    const user = userEvent.setup();
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, in_flight: false, latest_run: RUNNING_UPDATE } : h));
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    const drawer = within(screen.getByLabelText("Agency details"));
    expect(drawer.getByText("Stopped")).toBeTruthy();
    await user.click(drawer.getByRole("button", { name: "Retry" }));
    expect(updateMutate).toHaveBeenCalledWith(1);
  });

  it("retries a stopped update from its run card", async () => {
    const user = userEvent.setup();
    const stopped: AgencyRun = {
      ...RUNNING_UPDATE,
      status: "error",
      finished_at: "2026-10-13T05:01:00Z",
      steps: [
        { ...RUNNING_UPDATE.steps[0], status: "error", error: "StaticFetchError: HTTP 404 Not Found" },
        { ...RUNNING_UPDATE.steps[1], status: "not_run", started_at: null },
        { ...RUNNING_UPDATE.steps[2], status: "not_run" },
      ],
    };
    healthOverride = HEALTH.map((h) => (h.agency_id === 1 ? { ...h, latest_run: stopped } : h));
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    await user.click(within(screen.getByLabelText("Agency details")).getByRole("button", { name: "Retry" }));
    expect(updateMutate).toHaveBeenCalledWith(1);
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

  it("shows a failed disable on the page after its drawer has closed", () => {
    delState = { isPending: false, variables: 1, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("errors.network"));
  });

  it("shows a failed restore on the page after its drawer has closed", () => {
    restoreState = { isPending: false, variables: 2, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("errors.network"));
  });

  it("names the agency a failed disable was for", () => {
    delState = { isPending: false, variables: 1, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("admin.agencies.disable_failed", { name: "Aomori Bus" }));
  });

  it("names the agency a failed restore was for", () => {
    restoreState = { isPending: false, variables: 2, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("admin.agencies.restore_failed", { name: "Deleted Bus" }));
  });

  it("clears an earlier disable failure when a restore is started", async () => {
    const user = userEvent.setup();
    delState = { isPending: false, variables: 1, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Deleted Bus"));
    await user.click(within(screen.getByLabelText("Agency details")).getByRole("button", { name: /^Restore$/ }));
    expect(restoreMutate).toHaveBeenCalledWith(2);
    expect(delReset).toHaveBeenCalled();
  });

  it("clears an earlier restore failure when a disable is started", async () => {
    const user = userEvent.setup();
    restoreState = { isPending: false, variables: 2, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByText("Aomori Bus"));
    const drawer = within(screen.getByLabelText("Agency details"));
    await user.type(drawer.getByLabelText(/type the agency name to confirm/i), "Aomori Bus");
    await user.click(drawer.getByRole("button", { name: /^Disable$/ }));
    expect(restoreReset).toHaveBeenCalled();
  });

  it("narrows the list to behind-schedule agencies via the saved view", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /Behind schedule/ }));
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows every agency under a pressed All chip for a ?view= it does not offer", () => {
    wrap(<AdminAgenciesPage />, ["/admin/agencies?view=retired"]);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("filters rows by agency name via the search input", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "Deleted");
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("filters by a name typed with full-width letters", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "Ｄｅｌｅｔｅｄ");
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("does not claim there are no agencies while loading or after a failed load", () => {
    agenciesOverride = { data: undefined, isLoading: true, error: null };
    const { unmount } = wrap(<AdminAgenciesPage />);
    expect(screen.queryByText(i18n.t("admin.agencies.empty"))).toBeNull();
    unmount();
    agenciesOverride = { data: undefined, isLoading: false, error: new Error("boom") };
    wrap(<AdminAgenciesPage />);
    expect(screen.queryByText(i18n.t("admin.agencies.empty"))).toBeNull();
  });

  it("shows an empty-state row when the search matches nothing", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "nonexistent-agency");
    expect(screen.getByText("No agencies found.")).toBeTruthy();
  });
});
