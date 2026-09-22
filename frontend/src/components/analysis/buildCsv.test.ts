// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildCsv, type CsvColumn } from "./csv";
import type { RangeCtx } from "../../api/rangeContext";

type Row = { route: string; avg_min: number | null; note: string };

const columns: CsvColumn<Row>[] = [
  { header: "route_code", value: (r) => r.route },
  { header: "mean_minutes", value: (r) => r.avg_min },
  { header: "note", value: (r) => r.note },
];

function makeCtx(overrides: Partial<RangeCtx> = {}): RangeCtx {
  return {
    from: "2026-01-01",
    to: "2026-01-31",
    dow: "all",
    time_band: "all",
    service: "all",
    routes: [],
    ...overrides,
  };
}

describe("buildCsv", () => {
  it("emits a header row from the column definitions", () => {
    const rows = buildCsv<Row>([], columns);
    expect(rows[0]).toEqual(["route_code", "mean_minutes", "note"]);
  });

  it("maps each data row through the column accessors, in column order", () => {
    const data: Row[] = [{ route: "A1", avg_min: 3.4, note: "ok" }];
    const rows = buildCsv(data, columns);
    expect(rows[1]).toEqual(["A1", 3.4, "ok"]);
  });

  it("preserves cells needing csvText's own escaping (nulls, leading '=', embedded quotes)", () => {
    const data: Row[] = [{ route: "=SUM(A1)", avg_min: null, note: 'say "hi"' }];
    const rows = buildCsv(data, columns);
    expect(rows[1]).toEqual(["=SUM(A1)", null, 'say "hi"']);
  });

  it("omits the ctx metadata block when no ctx is given", () => {
    const rows = buildCsv<Row>([], columns);
    expect(rows).toHaveLength(1);
  });

  it("appends a blank line + a query-string metadata row built from ctxToQueryString when ctx is given", () => {
    const rows = buildCsv<Row>([], columns, makeCtx({ dow: "weekday", routes: ["A1", "B2"] }));
    // header, blank separator, then the query row
    expect(rows[1]).toEqual([]);
    expect(rows[2][0]).toBe("query");
    const qs = new URLSearchParams(String(rows[2][1]));
    expect(qs.get("from")).toBe("2026-01-01");
    expect(qs.get("dow")).toBe("weekday");
    expect(qs.get("routes")).toBe("A1,B2");
  });
});
