import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AgencyDiagnosticsDrawer } from "./AgencyDiagnosticsDrawer";

const probeMutate = vi.fn();
const reanalyzeMutate = vi.fn();
const standardsMutateAsync = vi.fn().mockResolvedValue([]);
const weightsMutateAsync = vi.fn().mockResolvedValue([]);

const DIAGNOSTICS = {
  agency_id: 1,
  agency_name: "Hokuriku Transit",
  feed_url: "https://feed.example.jp/tu.bin",
  static_url: "https://feed.example.jp/static.zip",
  ingest_strategy: "static_join",
  deleted_at: null,
  freshness: "fresh" as const,
  last_analyzed_at: "2026-09-20T01:00:00+00:00",
  latest_data_date: "2026-09-19",
  last_capture_at: "2026-09-20T02:30:00+00:00",
  rt_coverage: {
    complete: false,
    last_probed_at: "2026-09-19T03:00:00+00:00",
    fields: {
      stop_id: {
        present: true,
        coverage_pct: 98.5,
        sample_size: 1200,
        probed_at: "2026-09-19T03:00:00+00:00",
        expired: false,
        probed: true,
      },
      arr_delay: {
        present: false,
        coverage_pct: 0,
        sample_size: 1200,
        probed_at: "2026-09-19T03:00:00+00:00",
        expired: false,
        probed: true,
      },
      schedule_relationship_trip: {
        present: false,
        coverage_pct: null,
        sample_size: null,
        probed_at: null,
        expired: false,
        probed: false,
      },
      schedule_relationship_stop: {
        present: false,
        coverage_pct: 99.9,
        sample_size: 900,
        probed_at: "2026-01-02T03:00:00+00:00",
        expired: true,
        probed: true,
      },
    },
  },
  static_versions: [
    {
      version: "v2026-09-01",
      loaded_at: "2026-09-01T02:00:00+00:00",
      trips: 1204,
      vehicle_km: 18321.5,
      routes: 12,
      calendar_until: "2027-03-31",
      is_current: true,
    },
    {
      version: "v2026-04-01",
      loaded_at: "2026-04-01T02:00:00+00:00",
      trips: 1188,
      vehicle_km: null,
      routes: null,
      calendar_until: null,
      is_current: false,
    },
  ],
  clamp_history: [
    { date: "2026-09-19", clamp_pct: 1.49 },
    { date: "2026-09-20", clamp_pct: null },
  ],
  weather_station: { station_id: "47605", station_name: "Kanazawa", source: "jma_amedas", note: null },
  standards: [
    { route_code: "42", metric_type: "ewt_sec", threshold_value: 120, bonus_malus_rate: 1.5 },
  ],
  standards_count: 1,
  weights: [
    { route_code: null, weight: 1 },
    { route_code: "42", weight: 3 },
  ],
  weights_coverage: { routes_with_weights: 1, routes_total: 12 },
};

let diagnosticsState: { data: unknown; isLoading: boolean; error: unknown } = {
  data: DIAGNOSTICS,
  isLoading: false,
  error: null,
};

vi.mock("../../api/admin", () => ({
  useAgencyDiagnostics: () => diagnosticsState,
  useProbeAgencyFeed: () => ({ mutate: probeMutate, isPending: false, error: null, data: undefined }),
  useReanalyzeAgency: () => ({ mutate: reanalyzeMutate, isPending: false, error: null, data: undefined }),
  usePatchAgencyStandards: () => ({ mutateAsync: standardsMutateAsync, isPending: false, error: null }),
  usePatchAgencyWeights: () => ({ mutateAsync: weightsMutateAsync, isPending: false, error: null }),
}));

const AGENCY = {
  agency_id: 1,
  agency_name: "Hokuriku Transit",
  feed_url: "https://feed.example.jp/tu.bin",
  static_url: null,
  ingest_strategy: "static_join",
  trip_id_pattern: null,
  deleted_at: null,
};

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

function renderDrawer(overrides: Partial<React.ComponentProps<typeof AgencyDiagnosticsDrawer>> = {}) {
  return wrap(
    <AgencyDiagnosticsDrawer
      agency={AGENCY}
      onClose={() => {}}
      onDisable={() => {}}
      onRestore={() => {}}
      disablePending={false}
      {...overrides}
    />
  );
}

describe("AgencyDiagnosticsDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    diagnosticsState = { data: DIAGNOSTICS, isLoading: false, error: null };
  });

  it("renders the feed header", () => {
    renderDrawer();
    expect(screen.getByText("Hokuriku Transit")).toBeTruthy();
    expect(screen.getByText("https://feed.example.jp/tu.bin")).toBeTruthy();
    expect(screen.getByText("static_join")).toBeTruthy();
  });

  it("renders one RT diagnostics row per probed field with its verdict", () => {
    renderDrawer();
    expect(screen.getByText("stop_id")).toBeTruthy();
    expect(screen.getByText("arr_delay")).toBeTruthy();
    expect(screen.getByText("schedule_relationship_trip")).toBeTruthy();
    expect(screen.getByText("schedule_relationship_stop")).toBeTruthy();
    // present / absent / never probed / expired are four distinct states.
    expect(screen.getByText("Present")).toBeTruthy();
    expect(screen.getByText("Absent")).toBeTruthy();
    expect(screen.getByText("Not probed")).toBeTruthy();
    expect(screen.getByText("Expired")).toBeTruthy();
  });

  it("renders the static version timeline with the current version marked", () => {
    renderDrawer();
    expect(screen.getByText(/v2026-09-01/)).toBeTruthy();
    expect(screen.getByText(/v2026-04-01/)).toBeTruthy();
    expect(screen.getByText("Current")).toBeTruthy();
  });

  it("summarises standards, weights coverage and the weather station", () => {
    renderDrawer();
    expect(screen.getByText("Set on 1 route(s)")).toBeTruthy();
    expect(screen.getByText("1/12 route(s)")).toBeTruthy();
    expect(screen.getByText("Kanazawa (47605)")).toBeTruthy();
  });

  it("offers the three feed actions, with static reload disabled until its endpoint exists", () => {
    renderDrawer();
    expect(screen.getByRole("button", { name: /check the feed now/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /reload static data/i })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: /re-aggregate this agency only/i })).toBeTruthy();
  });

  it("triggers a probe and a re-aggregate for this agency", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /check the feed now/i }));
    expect(probeMutate).toHaveBeenCalledWith(1);
    await user.click(screen.getByRole("button", { name: /re-aggregate this agency only/i }));
    expect(reanalyzeMutate).toHaveBeenCalledWith(1);
  });

  it("shows a loading state instead of stale sections", () => {
    diagnosticsState = { data: undefined, isLoading: true, error: null };
    renderDrawer();
    expect(screen.queryByText("stop_id")).toBeNull();
  });

  // ── typed confirm ──────────────────────────────────────────────────────

  it("keeps the destructive action disabled until the exact name is typed", async () => {
    const user = userEvent.setup();
    const onDisable = vi.fn();
    renderDrawer({ onDisable });
    const button = screen.getByRole("button", { name: /^Disable$/ });
    expect(button).toHaveProperty("disabled", true);

    const input = screen.getByLabelText(/type the agency name to confirm/i);
    await user.type(input, "Hokuriku");
    expect(button).toHaveProperty("disabled", true);

    await user.clear(input);
    await user.type(input, "hokuriku transit");
    expect(button).toHaveProperty("disabled", true);

    await user.clear(input);
    await user.type(input, "Hokuriku Transit");
    expect(button).toHaveProperty("disabled", false);
    await user.click(button);
    expect(onDisable).toHaveBeenCalledWith(1);
  });

  it("ignores surrounding whitespace in the confirmation", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.type(screen.getByLabelText(/type the agency name to confirm/i), "  Hokuriku Transit  ");
    expect(screen.getByRole("button", { name: /^Disable$/ })).toHaveProperty("disabled", false);
  });

  it("offers restore instead of a typed confirm for an already-disabled agency", () => {
    const onRestore = vi.fn();
    renderDrawer({
      agency: { ...AGENCY, deleted_at: "2026-06-01T00:00:00Z" },
      onRestore,
    });
    expect(screen.queryByLabelText(/type the agency name to confirm/i)).toBeNull();
    expect(screen.getByRole("button", { name: /^Restore$/ })).toBeTruthy();
  });

  // ── editors ────────────────────────────────────────────────────────────

  it("saves an edited performance standard", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit performance standards/i }));
    const threshold = screen.getByLabelText(/threshold/i);
    await user.clear(threshold);
    await user.type(threshold, "90");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(standardsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [{ route_code: "42", metric_type: "ewt_sec", threshold_value: 90, bonus_malus_rate: 1.5 }],
        delete: [],
      },
    });
  });

  it("saves an edited ridership weight", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit ridership weights/i }));
    const inputs = screen.getAllByLabelText(/weight/i);
    await user.clear(inputs[1]);
    await user.type(inputs[1], "5");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(weightsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [
          { route_code: null, weight: 1 },
          { route_code: "42", weight: 5 },
        ],
        delete: [],
      },
    });
  });

  it("deletes the old key when a standard's route is renamed", async () => {
    // route_code is both a value the operator types and half the key the
    // backend upserts on, so a rename that only upserts leaves the original
    // row in the table, still feeding the bonus/malus calculation.
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit performance standards/i }));
    const route = screen.getByLabelText(/route/i);
    await user.clear(route);
    await user.type(route, "43");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(standardsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [{ route_code: "43", metric_type: "ewt_sec", threshold_value: 120, bonus_malus_rate: 1.5 }],
        delete: [{ route_code: "42", metric_type: "ewt_sec", threshold_value: 120, bonus_malus_rate: 1.5 }],
      },
    });
  });

  it("deletes the old key when a ridership weight's route is renamed", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit ridership weights/i }));
    await user.clear(screen.getByLabelText(/route/i));
    await user.type(screen.getByLabelText(/route/i), "77");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(weightsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [
          { route_code: null, weight: 1 },
          { route_code: "77", weight: 3 },
        ],
        delete: [{ route_code: "42", weight: 3 }],
      },
    });
  });

  it("still deletes the original when a renamed row is then removed", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit performance standards/i }));
    const route = screen.getByLabelText(/route/i);
    await user.clear(route);
    await user.type(route, "43");
    await user.click(screen.getByRole("button", { name: /remove row/i }));
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(standardsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [],
        delete: [{ route_code: "42", metric_type: "ewt_sec", threshold_value: 120, bonus_malus_rate: 1.5 }],
      },
    });
  });

  it("removes a row from the editor as a delete", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /edit performance standards/i }));
    await user.click(screen.getByRole("button", { name: /remove row/i }));
    await user.click(screen.getByRole("button", { name: /^Save$/ }));
    expect(standardsMutateAsync).toHaveBeenCalledWith({
      id: 1,
      body: {
        upsert: [],
        delete: [{ route_code: "42", metric_type: "ewt_sec", threshold_value: 120, bonus_malus_rate: 1.5 }],
      },
    });
  });
});
