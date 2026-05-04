import { useState, useRef, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useAsk } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ToolResult, TrendDay } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { TabFilterBar } from "../components/TabFilterBar";
import { DailyChart } from "../components/charts/DailyChart";

type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tool_call: { name: string; arguments: Record<string, unknown> } | null; result: ToolResult | null };

const SUGGESTIONS = ["今日の遅延ランキング", "雨天時の比較", "最近の傾向"];

export function AskTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const ask = useAsk(id);
  const routeNames = useRouteNames(id);

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
        { role: "assistant", text: r.answer, tool_call: r.tool_call, result: r.result },
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
      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "0 4px" }}>
        {msgs.length === 0 && (
          <div style={{ color: "var(--text-tertiary)", textAlign: "center", marginTop: 48 }}>
            質問を入力してください
          </div>
        )}
        {msgs.map((m, i) => (
          <Bubble key={i} msg={m} formatRoute={routeNames.format} />
        ))}
        {ask.isPending && (
          <div role="status" aria-live="polite" style={{ padding: 12, color: "var(--text-tertiary)" }}>
            考え中...
          </div>
        )}
        {ask.error && <ErrorBanner error={ask.error} onRetry={retry} />}
      </div>
      <div style={{ borderTop: "1px solid var(--border-soft)", padding: "12px 0", marginTop: 12 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {SUGGESTIONS.map((s) => (
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
            aria-label="質問を入力"
            placeholder="例: 系統5の今月の遅延傾向は?"
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
            送信
          </button>
        </form>
      </div>
    </div>
  );
}

function Bubble({ msg, formatRoute }: { msg: Msg; formatRoute: (rc: string | null | undefined) => string }) {
  const isUser = msg.role === "user";
  const result = !isUser && "result" in msg ? msg.result : null;
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
        {result ? <RichResult result={result} fallbackText={msg.text} formatRoute={formatRoute} /> : <span style={{ whiteSpace: "pre-wrap" }}>{msg.text}</span>}
        {!isUser && "result" in msg && (msg.tool_call || msg.result) && (
          <details style={{ marginTop: 8, color: "var(--text-tertiary)", fontSize: 12 }}>
            <summary style={{ cursor: "pointer" }}>詳細</summary>
            <pre style={{ overflowX: "auto", marginTop: 6, whiteSpace: "pre" }}>
              {JSON.stringify({ tool_call: msg.tool_call, result: msg.result }, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

function RichResult({
  result,
  fallbackText,
  formatRoute,
}: {
  result: ToolResult;
  fallbackText: string;
  formatRoute: (rc: string | null | undefined) => string;
}) {
  if (result.kind === "table" && result.rows && result.columns) {
    const cols = result.columns;
    const routeIdx = cols.findIndex((c) => c === "route_code");
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>{result.summary_jp}</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                {cols.map((c) => (
                  <th key={c} style={{ padding: "6px 10px", textAlign: "left", color: "var(--text-secondary)", fontWeight: 500 }}>
                    {c === "route_code" ? "系統" : c}
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
            …他{result.rows.length - 50}件
          </div>
        )}
      </div>
    );
  }
  if (result.kind === "kv" && result.pairs) {
    return (
      <div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>{result.summary_jp}</div>
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
        <div style={{ fontWeight: 600, marginBottom: 8 }}>{result.summary_jp}</div>
        <DailyChart days={result.series as TrendDay[]} height={200} />
      </div>
    );
  }
  // empty, text, or series with no points → plain text rendering
  return <span style={{ whiteSpace: "pre-wrap" }}>{fallbackText}</span>;
}
