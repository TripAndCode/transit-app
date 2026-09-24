import { ctxToQueryString, type RangeCtx } from "../../api/rangeContext";

export type CsvColumn<T> = { header: string; value: (row: T) => unknown };

/**
 * Turns a row array + declarative column list into the header+body matrix
 * `downloadCsv` expects, replacing the hand-rolled `[header, ...rows.map(...)]`
 * that used to be duplicated at every export call site. When `ctx` is given, a
 * blank separator line plus one `["query", ctxToQueryString(ctx)]` row is
 * appended, so the exported file records exactly the filters that produced it.
 */
export function buildCsv<T>(rows: T[], columns: CsvColumn<T>[], ctx?: RangeCtx | null): unknown[][] {
  const header = columns.map((c) => c.header);
  const body = rows.map((row) => columns.map((c) => c.value(row)));
  const matrix: unknown[][] = [header, ...body];
  if (ctx) matrix.push([], ["query", ctxToQueryString(ctx)]);
  return matrix;
}

/** Text cells are formula-safe, numeric cells keep their numeric meaning. */
export function csvText(rows: unknown[][]): string {
  return "\ufeff" + rows.map((row) => row.map((value) => {
    let s = value == null ? "" : String(value);
    if (typeof value === "string" && /^[\s]*[=+@\-\t\r\n]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  }).join(",")).join("\r\n");
}

/** Creates a temporary object URL + anchor to trigger a client-side file download, then cleans both up. */
export function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function downloadCsv(name: string, rows: unknown[][]) {
  triggerBlobDownload(
    new Blob([csvText(rows)], { type: "text/csv;charset=utf-8" }),
    name.replace(/[^\w.-]/g, "_") + ".csv",
  );
}
