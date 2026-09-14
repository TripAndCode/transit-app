/** Text cells are formula-safe, numeric cells keep their numeric meaning. */
export function csvText(rows: unknown[][]): string {
  return "\ufeff" + rows.map((row) => row.map((value) => {
    let s = value == null ? "" : String(value);
    if (typeof value === "string" && /^[\s]*[=+@\-\t\r\n]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  }).join(",")).join("\r\n");
}

export function downloadCsv(name: string, rows: unknown[][]) {
  const url = URL.createObjectURL(new Blob([csvText(rows)], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name.replace(/[^\w.-]/g, "_") + ".csv";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
