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
