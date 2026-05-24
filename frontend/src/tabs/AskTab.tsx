import { useMemo, useState, useRef, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useAsk } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ToolResult, TrendDay } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { TabFilterBar } from "../components/TabFilterBar";
import { DailyChart } from "../components/charts/DailyChart";

type AskCtxLite = {
  from: string;
  to: string;
  dow: string;
  time_band: string;
  service: string;
  routes?: string[];
};

type Msg =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      text: string;
      tool_call: { name: string; arguments: Record<string, unknown> } | null;
      result: ToolResult | null;
      ctx: AskCtxLite;
    };

export function AskTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const ask = useAsk(id);
  const routeNames = useRouteNames(id);

  const suggestions = useMemo(
    () => [
      t("ask.suggestion.today_ranking"),
      t("ask.suggestion.rain_compare"),
      t("ask.suggestion.recent_trend"),
    ],
    [t],
  );

  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [msgs, ask.isPending]);

  async function ask_(question: string) {
    if (ask.isPending) return;
    try {
      const r = await ask.mutateAsync({ question, ctx });
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: r.answer,
          tool_call: r.tool_call,
          result: r.result,
          ctx: r.ctx as unknown as AskCtxLite,
        },
      ]);
    } catch {
      // error renders via ask.error below
    }
  }

  async function submit(question: string) {
    const trimmed = question.trim();
    if (!trimmed || ask.isPending) return;
    setMsgs((m) => [...m, { role: "user", text: trimmed }]);
    setInput("");
    await ask_(trimmed);
  }

  async function retry() {
    const last = [...msgs].reverse().find((m) => m.role === "user");
    if (!last) return;
    await ask_(last.text);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", maxWidth: 760, margin: "0 auto" }}>
      <TabFilterBar />
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 12, color: "var(--text-tertiary)",
        margin: "4px 0 8px",
      }}>
        {t("nav.ask")}
        <InsightHint
          title={t("ask.hint.title")}
          body={
            <>
              {t("ask.hint.body_1")}
              <br /><br />
              {t("ask.hint.body_2")}
              <br /><br />
              {t("ask.hint.body_3_intro")}<strong>{t("ask.hint.body_3_strong")}</strong>{t("ask.hint.body_3_outro")}
            </>
          }
        />
      </div>
      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "0 4px" }}>
        {msgs.length === 0 && (
          <div style={{ color: "var(--text-tertiary)", textAlign: "center", marginTop: 48 }}>
            {t("ask.error_empty")}
          </div>
        )}
        {msgs.map((m, i) => (
          <Bubble key={i} msg={m} formatRoute={routeNames.format} t={t} />
        ))}
        {ask.isPending && (
          <div role="status" aria-live="polite" style={{ padding: 12, color: "var(--text-tertiary)" }}>
            {t("ask.thinking")}
          </div>
        )}
        {ask.error && <ErrorBanner error={ask.error} onRetry={retry} />}
      </div>
      <div style={{ borderTop: "1px solid var(--border-soft)", padding: "12px 0", marginTop: 12 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => submit(s)}
              disabled={ask.isPending}
              style={{
                background: "var(--bg-soft)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 999,
                padding: "4px 12px",
                fontSize: 13,
                color: "var(--text-secondary)",
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => { e.preventDefault(); submit(input); }}
          style={{ display: "flex", gap: 8 }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            aria-label={t("ask.input_aria")}
            placeholder={t("ask.input_placeholder")}
            style={{ flex: 1 }}
            disabled={ask.isPending}
          />
          <button
            type="submit"
            disabled={ask.isPending || !input.trim()}
            style={{
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              padding: "0 18px",
              borderRadius: "var(--radius)",
              opacity: ask.isPending || !input.trim() ? 0.5 : 1,
            }}
          >
            {t("ask.submit")}
          </button>
        </form>
      </div>
    </div>
  );
}

function Bubble({
  msg,
  formatRoute,
  t,
}: {
  msg: Msg;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  const isUser = msg.role === "user";
  const result = !isUser && "result" in msg ? msg.result : null;
  const ctx = !isUser && "ctx" in msg ? msg.ctx : null;
  const wide = !isUser && (result?.kind === "table" || result?.kind === "series");

  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", margin: "12px 0" }}>
      <div
        style={{
          maxWidth: wide ? "100%" : "85%",
          width: wide ? "100%" : undefined,
          padding: "10px 14px",
          background: isUser ? "var(--accent-soft)" : "var(--bg-surface)",
          border: isUser ? "none" : "1px solid var(--border-soft)",
          borderRadius: "var(--radius-lg)",
          whiteSpace: isUser ? "pre-wrap" : undefined,
        }}
      >
        {result ? (
          <RichResult result={result} fallbackText={msg.text} formatRoute={formatRoute} ctx={ctx} t={t} />
        ) : (
          // The assistant response `text` is server-rendered by the backend
          // formatter, already in the locale the request asked for via
          // Accept-Language (see api/middleware/locale.py). Rendered as-is.
          <span style={{ whiteSpace: "pre-wrap" }}>{msg.text}</span>
        )}
        {!isUser && "result" in msg && (msg.tool_call || msg.result) && (
          <details style={{ marginTop: 8, color: "var(--text-tertiary)", fontSize: 12 }}>
            <summary style={{ cursor: "pointer" }}>{t("common.details")}</summary>
            <pre style={{ overflowX: "auto", marginTop: 6, whiteSpace: "pre" }}>
              {JSON.stringify({ tool_call: msg.tool_call, result: msg.result }, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

function CtxLine({ ctx, t }: { ctx: AskCtxLite | null; t: TFunction }) {
  if (!ctx) return null;
  const bits: string[] = [t("ask.ctx.range", { from: ctx.from, to: ctx.to })];
  if (ctx.dow && ctx.dow !== "all") bits.push(t("ask.ctx.dow", { value: ctx.dow }));
  if (ctx.time_band && ctx.time_band !== "all") bits.push(t("ask.ctx.time_band", { value: ctx.time_band }));
  if (ctx.service && ctx.service !== "all") bits.push(t("ask.ctx.service", { value: ctx.service }));
  if (ctx.routes && ctx.routes.length > 0) bits.push(t("ask.ctx.routes", { value: ctx.routes.join(", ") }));
  return (
    <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: -4, marginBottom: 8 }}>
      {bits.join(" ・ ")}
    </div>
  );
}

function RichResult({
  result,
  fallbackText,
  formatRoute,
  ctx,
  t,
}: {
  result: ToolResult;
  fallbackText: string;
  formatRoute: (rc: string | null | undefined) => string;
  ctx: AskCtxLite | null;
  t: TFunction;
}) {
  if (result.kind === "table" && result.rows && result.columns) {
    const cols = result.columns;
    const routeIdx = cols.findIndex((c) => c === "route_code");
    return (
      <div>
        {/* `summary` is the backend-formatted, locale-aware summary
            (rendered by pipeline.query.tools._summary on the server). */}
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.summary}</div>
        <CtxLine ctx={ctx} t={t} />
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                {cols.map((c) => (
                  <th key={c} style={{ padding: "6px 10px", textAlign: "left", color: "var(--text-secondary)", fontWeight: 500 }}>
                    {c === "route_code" ? t("ask.col.route") : c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 50).map((row, i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--border-soft)" }}>
                  {row.map((cell, j) => (
                    <td key={j} style={{ padding: "6px 10px" }}>
                      {j === routeIdx
                        ? formatRoute(cell as string)
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
        <CtxLine ctx={ctx} t={t} />
        <table style={{ borderCollapse: "collapse", fontSize: 14 }}>
          <tbody>
            {result.pairs.map(([k, v], i) => (
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
  if (result.kind === "series" && result.series && result.series.length > 0) {
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{result.summary}</div>
        <CtxLine ctx={ctx} t={t} />
        <DailyChart days={result.series as TrendDay[]} height={200} />
      </div>
    );
  }
  // empty, text, or series with no points → plain text rendering. `fallbackText`
  // is the backend-formatted answer — opaque on the client (see T-EXTRA-B note).
  return <span style={{ whiteSpace: "pre-wrap" }}>{fallbackText}</span>;
}
