import { describe, expect, expectTypeOf, it } from "vitest";
import type {
  CouncilSummaryRow,
  DelayCertificateRow,
  DwellRunPayload,
  RankingRow,
  ReportResponse,
  ReportType,
  TrendPayload,
} from "./types";

/** `ReportResponse` is a discriminated union on `report_type`, so a consumer
 *  reaches a report's own row shape by testing that field -- never by casting
 *  through `unknown`. These assertions are compile-time only (`expectTypeOf`
 *  erases at runtime); `npm run typecheck` is what actually enforces them. */
describe("ReportResponse discriminates on report_type", () => {
  it("narrows trend to its single structured payload", () => {
    const res = {} as ReportResponse;
    if (res.report_type === "trend") {
      expectTypeOf(res.rows).toEqualTypeOf<TrendPayload[]>();
      expectTypeOf<TrendPayload["dow_band"]["grid"]>().toBeArray();
      expectTypeOf<TrendPayload["days"]>().toBeArray();
      // A tuple-row report's shape must NOT be reachable from this branch.
      expectTypeOf(res.rows).not.toEqualTypeOf<RankingRow[]>();
    }
    expect(true).toBe(true);
  });

  it("narrows ranking to positional tuple rows", () => {
    const res = {} as ReportResponse;
    if (res.report_type === "ranking" || res.report_type === "ranking_best") {
      expectTypeOf(res.rows).toEqualTypeOf<RankingRow[]>();
      expectTypeOf<RankingRow[0]>().toEqualTypeOf<string>();
      expectTypeOf<RankingRow[1]>().toEqualTypeOf<string | null>();
      expectTypeOf<RankingRow[5]>().toEqualTypeOf<number>();
      expectTypeOf(res.rows).not.toEqualTypeOf<TrendPayload[]>();
    }
    expect(true).toBe(true);
  });

  it("narrows dwell_run, council_summary and delay_certificate", () => {
    const res = {} as ReportResponse;
    if (res.report_type === "dwell_run") {
      expectTypeOf(res.rows).toEqualTypeOf<DwellRunPayload[]>();
    } else if (res.report_type === "council_summary") {
      expectTypeOf(res.rows).toEqualTypeOf<CouncilSummaryRow[]>();
    } else if (res.report_type === "delay_certificate") {
      expectTypeOf(res.rows).toEqualTypeOf<DelayCertificateRow[]>();
    }
    expect(true).toBe(true);
  });

  it("rejects a report_type the endpoint does not serve", () => {
    // Asserted as an assignment, not as `expectTypeOf(...).toEqualTypeOf<
    // "route_forecast">()`: that comparison fails whether or not the member
    // exists, because the field is the whole union either way, so the
    // directive stays satisfied and the test never notices the addition.
    //
    // @ts-expect-error route_forecast is an Analysis-tab list entry served by
    // /forecast, never a /reports/{report_type} response. Adding it to
    // ReportType makes this assignment legal, and TS then reports this
    // directive as unused — which is the failure this test exists to cause.
    const served: ReportType = "route_forecast";
    expect(served).toBe("route_forecast");
  });
});
