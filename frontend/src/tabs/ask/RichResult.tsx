import type { TFunction } from "i18next";
import type { ToolResult, TrendDay } from "../../api/types";
import { DailyChart } from "../../components/charts/DailyChart";

/** Render a tool result as a table / key-value list / chart, falling back to
 *  plain text for empty or text-kind results. */
export function RichResult({
  result,
  fallbackText,
  formatRoute,
  t,
}: {
  result: ToolResult;
  fallbackText: string;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  if (result.kind === "table" && result.rows && result.columns) {
    const cols = result.columns;
    const routeIdx = cols.findIndex((c) => c === "route_code");
    const serviceTypeIdx = cols.findIndex((c) => c === "service_type");
    // The on-time tools' trailing `low_confidence` column is a caveat flag
    // (95% Wilson interval too wide to trust the percentage — see
    // pipeline/stats.py), not a plain value; render a short marker only
    // when true rather than the raw "true"/"false".
    const lowConfIdx = cols.findIndex((c) => c === "low_confidence");
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.summary}</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                {cols.map((c) => (
                  <th
                    key={c}
                    style={{
                      padding: "6px 10px",
                      textAlign: "left",
                      color: "var(--text-secondary)",
                      fontWeight: 500,
                    }}
                  >
                    {t(`ask.col.${c}`, { defaultValue: c })}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 50).map((row, i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--border-soft)" }}>
                  {(row as unknown[]).map((cell, j) => (
                    <td key={j} style={{ padding: "6px 10px" }}>
                      {j === routeIdx
                        ? formatRoute(cell as string)
                        : j === serviceTypeIdx && cell != null
                          ? t(`common.service_value.${String(cell)}`, { defaultValue: String(cell) })
                          : j === lowConfIdx
                            ? cell
                              ? t("ask.low_confidence_mark")
                              : ""
                            : cell == null
                              ? "—"
                              : typeof cell === "number"
                                ? cell.toLocaleString()
                                : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {result.rows.length > 50 && (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 6 }}>
            {t("ask.more_rows", { count: result.rows.length - 50 })}
          </div>
        )}
      </div>
    );
  }

  if (result.kind === "kv" && result.pairs) {
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.summary}</div>
        <table style={{ borderCollapse: "collapse", fontSize: 14 }}>
          <tbody>
            {(result.pairs as [string, unknown][]).map(([k, v], i) => (
              <tr key={i}>
                <td style={{ padding: "4px 12px 4px 0", color: "var(--text-secondary)" }}>{k}</td>
                <td style={{ padding: "4px 0" }}>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (result.kind === "series" && result.series && (result.series as unknown[]).length > 0) {
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.summary}</div>
        <DailyChart days={result.series as TrendDay[]} height={200} />
      </div>
    );
  }

  // empty, text, or series with no points → plain text
  return <span style={{ whiteSpace: "pre-wrap" }}>{fallbackText}</span>;
}
