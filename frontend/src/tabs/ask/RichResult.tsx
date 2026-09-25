import { useRef, type ReactNode } from "react";
import type { TFunction } from "i18next";
import type { ToolResult, TrendDay } from "../../api/types";
import { DailyChart } from "../../components/charts/DailyChart";
import { formatNumber } from "../../utils/format";
import { exportSvgAsPng } from "./chartPng";
import { buildNextStepChips, type NextStepAction } from "./nextStepChips";
import { conditionsLabel, formatWindow, provenancePath, sampleCount, toolLabel } from "./provenance";
import { SHARED_TABLE } from "../../components/tableStyles";

type Conditions = { dow?: string; time_band?: string; service?: string } | null;

/** Render a tool result as a table / key-value list / chart, falling back to
 *  plain text for empty or text-kind results. Table/kv/series results are
 *  wrapped in the Ask evidence card: a provenance badge, the chart/table
 *  first, a one-sentence summary, next-step chips, and a "where this comes
 *  from" disclosure — all derived from fields the Ask API already returns
 *  (`tool`, `args`, the message's persisted `conditions`). */
export function RichResult({
  result,
  fallbackText,
  formatRoute,
  t,
  tool,
  args,
  conditions,
  onChip,
}: {
  result: ToolResult;
  fallbackText: string;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
  tool: string | null;
  args: Record<string, unknown> | null;
  conditions?: Conditions;
  onChip?: (action: NextStepAction) => void;
}) {
  if (result.kind === "table" && result.rows && result.columns) {
    return (
      <EvidenceCard result={result} tool={tool} args={args} conditions={conditions} onChip={onChip} t={t}>
        <ResultTable result={result} formatRoute={formatRoute} t={t} />
      </EvidenceCard>
    );
  }

  if (result.kind === "kv" && result.pairs) {
    return (
      <EvidenceCard result={result} tool={tool} args={args} conditions={conditions} onChip={onChip} t={t}>
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
      </EvidenceCard>
    );
  }

  if (result.kind === "series" && result.series && (result.series as unknown[]).length > 0) {
    return (
      <EvidenceCard result={result} tool={tool} args={args} conditions={conditions} onChip={onChip} t={t}>
        <DailyChart days={result.series as TrendDay[]} height={200} brushable={false} />
      </EvidenceCard>
    );
  }

  // empty, text, or series with no points → plain text, no evidence chrome
  return <span style={{ whiteSpace: "pre-wrap" }}>{fallbackText}</span>;
}

function ResultTable({
  result,
  formatRoute,
  t,
}: {
  result: ToolResult;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  const cols = result.columns!;
  const rows = result.rows!;
  const routeIdx = cols.findIndex((c) => c === "route_code");
  const serviceTypeIdx = cols.findIndex((c) => c === "service_type");
  // The on-time tools' trailing `low_confidence` column is a caveat flag
  // (95% Wilson interval too wide to trust the percentage — see
  // pipeline/stats.py), not a plain value; render a short marker only
  // when true rather than the raw "true"/"false".
  const lowConfIdx = cols.findIndex((c) => c === "low_confidence");
  return (
    <div style={{ overflowX: "auto" }}>
      {/* The element style is the shared one. The cells are not: this table
          is a compact inline result inside an answer, so its headers stay
          sentence-case and tighter than the uppercase `th()` every page-level
          table uses. */}
      <table style={SHARED_TABLE}>
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
          {rows.slice(0, 50).map((row, i) => (
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
                            ? formatNumber(cell)
                            : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 50 && (
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 6 }}>
          {t("ask.more_rows", { count: rows.length - 50 })}
        </div>
      )}
    </div>
  );
}

function EvidenceCard({
  result,
  tool,
  args,
  conditions,
  onChip,
  t,
  children,
}: {
  result: ToolResult;
  tool: string | null;
  args: Record<string, unknown> | null;
  conditions?: Conditions;
  onChip?: (action: NextStepAction) => void;
  t: TFunction;
  children: ReactNode;
}) {
  const path = provenancePath({ tool });
  const label = toolLabel(tool, t);
  const window_ = formatWindow(args ?? null, t);
  const count = sampleCount(result);
  const chips = buildNextStepChips({ tool, args: args ?? null, conditions: conditions ?? null, resultKind: result.kind, t });
  const cardRef = useRef<HTMLDivElement | null>(null);

  function handleChip(action: NextStepAction) {
    if (action.kind === "export_png") {
      const svg = cardRef.current?.querySelector("svg");
      // A fixed name, not a timestamp: Date.now() is an impure call the
      // React Compiler rejects here, and the browser already disambiguates
      // repeat downloads of the same filename on its own (chart (1).png, …).
      if (svg) exportSvgAsPng(svg, "ask-chart.png");
      return;
    }
    onChip?.(action);
  }

  return (
    <div ref={cardRef} className="ask-evidence-card">
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          fontSize: 12,
          color: "var(--text-tertiary)",
        }}
      >
        <span
          style={{
            fontWeight: 600,
            color: path === "sql" ? "var(--accent)" : "var(--text-secondary)",
            background: "var(--bg-soft)",
            border: "1px solid var(--border-soft)",
            borderRadius: 20,
            padding: "2px 9px",
          }}
        >
          {t(path === "sql" ? "ask.evidence.badge.sql" : "ask.evidence.badge.llm")}
        </span>
        {label && <span>{label}</span>}
        {window_ && <span>{window_}</span>}
        {count != null && <span>{t("ask.evidence.sample_count", { count })}</span>}
      </div>

      {children}

      <div style={{ marginTop: 8, fontFamily: "var(--font-display)", fontSize: "var(--text-base)" }}>
        {result.summary}
      </div>

      {chips.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          {chips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => handleChip(chip.action)}
              style={{
                padding: "5px 12px",
                background: "var(--bg-soft)",
                border: "1px solid var(--border-soft)",
                borderRadius: 20,
                fontSize: 12,
                color: "var(--accent)",
                cursor: "pointer",
              }}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      <details className="ask-evidence-disclosure" style={{ marginTop: 10, fontSize: 12, color: "var(--text-tertiary)" }}>
        <summary style={{ cursor: "pointer" }}>{t("ask.evidence.disclosure_title")}</summary>
        <dl style={{ margin: "6px 0 0", display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px" }}>
          {typeof (args?.route ?? args?.route_code) === "string" && (
            <>
              <dt>{t("ask.evidence.disclosure_route")}</dt>
              <dd>{formatRouteFallback(args)}</dd>
            </>
          )}
          <dt>{t("ask.evidence.disclosure_conditions")}</dt>
          <dd>{conditionsLabel(conditions, t)}</dd>
          {label && (
            <>
              <dt>{t("ask.evidence.disclosure_aggregation")}</dt>
              <dd>{label}</dd>
            </>
          )}
          <dt>{t("ask.evidence.disclosure_confidence")}</dt>
          <dd>{t(path === "sql" ? "ask.evidence.confidence.sql" : "ask.evidence.confidence.llm")}</dd>
        </dl>
      </details>
    </div>
  );
}

// The disclosure shows the raw route code (not the caller's route-name
// formatter): it's a compact "how this was scoped" record, and the table
// above already renders the friendly name via `formatRoute` where relevant.
function formatRouteFallback(args: Record<string, unknown> | null): string {
  const raw = args?.route ?? args?.route_code;
  return typeof raw === "string" ? raw : "";
}
