import { afterEach, describe, expect, it, vi } from "vitest";
import * as client from "../../api/client";
import { ApiError } from "../../api/client";
import type { Snapshot, SnapshotSpec } from "../../api/snapshots";
import type { DefinitionMeta, ReportResponse } from "../../api/types";
import { PRINT_SNAPSHOT_WAIT_MS, sameFigures, snapshotForPrint } from "./printSnapshot";

const DEFINITION: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: "2026-11-05T15:30:00Z",
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

const SHOWN = {
  report_type: "council_summary",
  rendered_at: "2026-11-10T03:00:00Z",
  text: "On time 22.6%, average delay 2.5 min.",
  rows: [[22.6, 2.5, 540357, 2130, 2100, 98.6, 4.2]],
  ctx: { from: "2026-05-14", to: "2026-05-14", dow: "all", time_band: "all", service: "all", routes: [] },
  definition: DEFINITION,
  scope_applied: { from: true, to: true },
  delivered_scope_applied: { from: true, to: true },
  covered_days: 1,
  window_days: 1,
  delivered_missing: null,
} as ReportResponse;

const SPEC: SnapshotSpec = { screen: "council_report", agencyId: 1, query: "from=2026-05-14&to=2026-05-14" };
const ID = "AbCdEfGhIjKlMnOpQrStUv";
const CREATED = {
  snapshot_id: ID,
  path: `/s/${ID}`,
  created_at: "2026-11-10T03:00:05Z",
  screen: "council_report",
  agency_id: 1,
  method_version: "2026.11.0",
};

/** Every object's keys in reverse, as a jsonb round trip may reorder them. */
function reordered<T>(value: T): T {
  if (Array.isArray(value)) return value.map(reordered) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).reverse().map(([k, v]) => [k, reordered(v)])) as T;
  }
  return value;
}

/** The stored snapshot as its read hands it back: each language's response
 *  served a moment after the one on the page. */
function stored(en: ReportResponse, ja: ReportResponse = en): Snapshot {
  const later = (r: ReportResponse) => reordered({ ...r, rendered_at: "2026-11-10T03:00:04Z" });
  return {
    snapshot_id: ID,
    created_at: CREATED.created_at,
    screen: "council_report",
    agency_id: 1,
    agency_name: "Aomori",
    params: {},
    payloads: { report: { ja: later(ja), en: later(en) }, routes: [], credit: { ja: null, en: null } },
    method_version: "2026.11.0",
    title: null,
    note: null,
    capability_tier: "full",
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("sameFigures", () => {
  it("holds two responses equal whatever their key order or the moment each was served", () => {
    expect(sameFigures(SHOWN, reordered({ ...SHOWN, rendered_at: "2026-11-11T00:00:00Z" }))).toBe(true);
  });

  it("tells apart responses that differ in any figure, its prose or its stamp", () => {
    expect(sameFigures(SHOWN, { ...SHOWN, rows: [[22.7, 2.5, 540357, 2130, 2100, 98.6, 4.2]] } as ReportResponse)).toBe(false);
    expect(sameFigures(SHOWN, { ...SHOWN, text: "On time 22.7%." })).toBe(false);
    expect(sameFigures(SHOWN, { ...SHOWN, definition: { ...DEFINITION, built_at: "2026-11-08T03:00:00Z" } })).toBe(false);
  });
});

describe("snapshotForPrint", () => {
  it("stores the screen, reads it back, and hands over its page and method when it holds the figures on the page", async () => {
    const post = vi.spyOn(client, "apiPost").mockResolvedValue(CREATED);
    const get = vi.spyOn(client, "apiGet").mockResolvedValue(stored(SHOWN));
    await expect(snapshotForPrint(SPEC, SHOWN, "en")).resolves.toEqual({
      kind: "stored",
      path: `/s/${ID}`,
      methodVersion: "2026.11.0",
    });
    expect(post).toHaveBeenCalledWith(
      "/api/admin/snapshots",
      { screen: "council_report", agency_id: 1, query: { from: "2026-05-14", to: "2026-05-14" } },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(get).toHaveBeenCalledWith(`/api/snapshots/${ID}`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("compares the stored response in the page's own language", async () => {
    vi.spyOn(client, "apiPost").mockResolvedValue(CREATED);
    const japanese = { ...SHOWN, text: "定時率 22.6%" }; // i18n-ignore: fixture prose
    vi.spyOn(client, "apiGet").mockResolvedValue(stored(SHOWN, japanese));
    await expect(snapshotForPrint(SPEC, japanese, "ja")).resolves.toMatchObject({ kind: "stored" });
    await expect(snapshotForPrint(SPEC, SHOWN, "ja")).resolves.toEqual({ kind: "moved" });
  });

  it("says the figures moved when the stored ones are not those on the page", async () => {
    vi.spyOn(client, "apiPost").mockResolvedValue(CREATED);
    const rebuilt = { ...SHOWN, rows: [[30.1, 1.9, 540357, 2130, 2100, 98.6, 4.2]] } as ReportResponse;
    vi.spyOn(client, "apiGet").mockResolvedValue(stored(rebuilt));
    await expect(snapshotForPrint(SPEC, SHOWN, "en")).resolves.toEqual({ kind: "moved" });
  });

  it("fails, never throws, when the create or the read is refused", async () => {
    vi.spyOn(client, "apiPost").mockRejectedValueOnce(new ApiError(409, "the aggregates changed")).mockResolvedValue(CREATED);
    vi.spyOn(client, "apiGet").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(snapshotForPrint(SPEC, SHOWN, "en")).resolves.toEqual({ kind: "failed" });
    await expect(snapshotForPrint(SPEC, SHOWN, "en")).resolves.toEqual({ kind: "failed" });
  });

  it("fails when the stored snapshot has no response in the page's language", async () => {
    vi.spyOn(client, "apiPost").mockResolvedValue(CREATED);
    const onlyJa = stored(SHOWN);
    delete onlyJa.payloads.report?.en;
    vi.spyOn(client, "apiGet").mockResolvedValue(onlyJa);
    await expect(snapshotForPrint(SPEC, SHOWN, "en")).resolves.toEqual({ kind: "failed" });
  });

  it("stops its requests as soon as the print is called off", async () => {
    vi.useFakeTimers();
    vi.spyOn(client, "apiPost").mockImplementation(
      (_path, _body, opts) =>
        new Promise((_, reject) => opts?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
    );
    const calledOff = new AbortController();
    const result = snapshotForPrint(SPEC, SHOWN, "en", calledOff.signal);
    let settled = false;
    void result.then(() => (settled = true));
    calledOff.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(true);
    await expect(result).resolves.toEqual({ kind: "failed" });
  });

  it("stops waiting after its bound, so a print is never held by a request that does not answer", async () => {
    vi.useFakeTimers();
    vi.spyOn(client, "apiPost").mockImplementation(
      (_path, _body, opts) =>
        new Promise((_, reject) => opts?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))),
    );
    const result = snapshotForPrint(SPEC, SHOWN, "en");
    await vi.advanceTimersByTimeAsync(PRINT_SNAPSHOT_WAIT_MS - 1);
    let settled = false;
    void result.then(() => (settled = true));
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toEqual({ kind: "failed" });
  });
});
