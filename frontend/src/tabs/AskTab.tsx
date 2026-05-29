import { useMemo, useState, useRef, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useAsk, usePostEditAction } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ToolResult, TrendDay } from "../api/types";
import type { IntentSignature } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { TabFilterBar } from "../components/TabFilterBar";
import { DailyChart } from "../components/charts/DailyChart";
import { AskModeToggle } from "../components/AskModeToggle";
import type { AskMode } from "../components/AskModeToggle";
import { AskChips } from "../components/AskChips";
import { AskAutocomplete } from "../components/AskAutocomplete";
import { AskBuildForm } from "../components/AskBuildForm";
import { ConfidencePill } from "../components/ConfidencePill";

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
      signature_hash?: string | null;
      confidence?: number | null;
      canonical_args?: Record<string, unknown> | null;
      cache_outcome?: "hit" | "miss" | null;
    };

type HistTurn = { question: string; tool?: string | null; args?: Record<string, unknown> | null };

function buildHistory(msgs: Msg[]): HistTurn[] {
  const turns: HistTurn[] = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    // a user message followed by an assistant message = one turn
    if (m.role === "user") {
      const next = msgs[i + 1];
      const tc = next && next.role === "assistant" ? next.tool_call : null;
      turns.push({ question: m.text, tool: tc?.name ?? null, args: tc?.arguments ?? null });
    }
  }
  return turns.slice(-3);
}

/** Build a short human-readable summary of tool args, e.g. "metric=avg_delay, n=10" */
function previewFromArgs(args: Record<string, unknown> | null | undefined): string | undefined {
  if (!args) return undefined;
  const entries = Object.entries(args)
    .slice(0, 4)
    .map(([k, v]) => `${k}=${String(v)}`);
  return entries.join(", ") || undefined;
}

export function AskTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [ctx] = useRangeContext();
  const ask = useAsk(id);
  const postEditAction = usePostEditAction(id ?? 0);
  const routeNames = useRouteNames(id);

  const [mode, setMode] = useState<AskMode>("chat");
  const [buildInitial, setBuildInitial] = useState<IntentSignature | null>(null);
  const [showAutocomplete, setShowAutocomplete] = useState(false);

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
      const history = buildHistory(msgs);
      const r = await ask.mutateAsync({ question, ctx, history });
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: r.answer,
          tool_call: r.tool_call,
          result: r.result,
          ctx: r.ctx as unknown as AskCtxLite,
          signature_hash: r.signature_hash ?? null,
          confidence: r.confidence ?? null,
          canonical_args: r.canonical_args ?? null,
          cache_outcome: r.cache_outcome ?? null,
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
    setShowAutocomplete(false);
    await ask_(trimmed);
  }

  async function submitFromAutocomplete(q: string) {
    setInput(q);
    await submit(q);
  }

  /**
   * Build-mode form submit.
   *
   * Encodes the structured intent as a compact question string that the
   * backend will parse as a JSON-mode signature directly.  The ``__build__``
   * sentinel prefix prevents the backend from storing the machine-generated
   * string as a human-readable ``last_question`` in the intent cache, so it
   * never surfaces as a chip or autocomplete suggestion.
   */
  async function submitStructured(tool: string, args: Record<string, unknown>) {
    const question = `__build__ ${tool} ${JSON.stringify(args)}`;
    setMode("chat");
    setBuildInitial(null);
    await submit(question);
  }

  async function retry() {
    const last = [...msgs].reverse().find((m) => m.role === "user");
    if (!last) return;
    await ask_(last.text);
  }

  /**
   * Switch to build mode pre-populated with msg's canonical_args and tool.
   * Also fires a fire-and-forget edit-action POST to record the user's verdict.
   */
  function switchToEditMode(msg: Extract<Msg, { role: "assistant" }>) {
    setMode("build");
    setBuildInitial({
      tool: msg.tool_call?.name ?? "",
      args: msg.canonical_args ?? msg.tool_call?.arguments ?? {},
      confidence: msg.confidence ?? 0,
      rationale: null,
    });

    if (msg.signature_hash && id != null) {
      postEditAction
        .mutateAsync({ signature_hash: msg.signature_hash, action: "edited" })
        .catch(console.warn);
    }
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

      {/* Mode toggle — always visible above the message area */}
      <div style={{ marginBottom: 10 }}>
        <AskModeToggle value={mode} onChange={setMode} />
      </div>

      {/* Build form (shown in build mode, above chat history) */}
      {mode === "build" && id != null && (
        <div style={{
          marginBottom: 12,
          padding: "12px 16px",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
        }}>
          <AskBuildForm
            agencyId={id}
            initialValue={buildInitial}
            onSubmit={(tool, args) => submitStructured(tool, args)}
            onCancel={() => { setMode("chat"); setBuildInitial(null); }}
          />
        </div>
      )}

      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "0 4px" }}>
        {/* Chips: shown in chat mode when there are no messages yet */}
        {mode === "chat" && msgs.length === 0 && id != null && (
          <AskChips agencyId={id} onPick={(q) => submit(q)} />
        )}

        {msgs.length === 0 && mode === "chat" && (
          <div style={{ color: "var(--text-tertiary)", textAlign: "center", marginTop: 48 }}>
            {t("ask.empty_state")}
          </div>
        )}

        {msgs.map((m, i) => {
          if (m.role === "assistant") {
            const confidence = m.confidence ?? null;
            const toolName = m.tool_call?.name ?? "";
            const argsPreview = previewFromArgs(m.canonical_args ?? m.tool_call?.arguments);

            return (
              <div key={i}>
                {/* Low-confidence block card — shown AFTER the answer as a strong warning.
                    TODO: Phase ③ — block execution on the backend before LLM call when
                    confidence < 0.5, and show this card instead of the answer. Currently
                    the backend always executes; we surface the card post-answer. */}
                {confidence !== null && confidence < 0.5 && toolName && (
                  <LowConfCard
                    toolName={toolName}
                    argsPreview={argsPreview}
                    onEdit={() => switchToEditMode(m)}
                    t={t}
                  />
                )}
                <Bubble msg={m} formatRoute={routeNames.format} t={t} />
                {/* Confidence pill — only when confidence is available and >= 0.5 */}
                {confidence !== null && confidence >= 0.5 && toolName && (
                  <div style={{ paddingLeft: 4, paddingBottom: 4 }}>
                    <ConfidencePill
                      confidence={confidence}
                      toolName={toolName}
                      argsPreview={argsPreview}
                      onEdit={() => switchToEditMode(m)}
                    />
                  </div>
                )}
              </div>
            );
          }
          return <Bubble key={i} msg={m} formatRoute={routeNames.format} t={t} />;
        })}

        {ask.isPending && (
          <div role="status" aria-live="polite" style={{ padding: 12, color: "var(--text-tertiary)" }}>
            {t("ask.thinking")}
          </div>
        )}
        {ask.error && <ErrorBanner error={ask.error} onRetry={retry} />}
      </div>

      {/* Input area — only shown in chat mode */}
      {mode === "chat" && (
        <div style={{ borderTop: "1px solid var(--border-soft)", padding: "12px 0", marginTop: 12 }}>
          {/* Legacy suggestion chips — hidden once we have dynamic chips from AskChips,
              but kept here as a fallback for the flag-off path */}
          {msgs.length === 0 && (
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
          )}
          <div style={{ position: "relative" }}>
            <form
              onSubmit={(e) => { e.preventDefault(); submit(input); }}
              style={{ display: "flex", gap: 8 }}
            >
              <input
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setShowAutocomplete(true);
                }}
                onFocus={() => setShowAutocomplete(true)}
                onBlur={() => {
                  // Delay dismiss so click on autocomplete item can fire first
                  setTimeout(() => setShowAutocomplete(false), 150);
                }}
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
            {/* Autocomplete dropdown — shown when focused + 2+ chars typed */}
            {showAutocomplete && input.trim().length >= 2 && id != null && (
              <AskAutocomplete
                agencyId={id}
                q={input}
                onPick={(q) => submitFromAutocomplete(q)}
                onDismiss={() => setShowAutocomplete(false)}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Inline block card shown when confidence < 0.5. Extraction skipped (single usage). */
function LowConfCard({
  toolName,
  argsPreview,
  onEdit,
  t,
}: {
  toolName: string;
  argsPreview?: string;
  onEdit: () => void;
  t: TFunction;
}) {
  return (
    <div
      role="alert"
      style={{
        borderLeft: "3px solid var(--accent)",
        background: "rgba(255, 220, 100, 0.18)",
        padding: "8px 12px",
        borderRadius: "0 var(--radius) var(--radius) 0",
        marginBottom: 6,
        fontSize: 13,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {t("ask.lowconf.title")}
      </div>
      <div style={{ color: "var(--text-secondary)", marginBottom: 8 }}>
        <span style={{ fontWeight: 500 }}>{toolName}</span>
        {argsPreview && (
          <span style={{ marginLeft: 6, color: "var(--text-tertiary)", fontSize: 12 }}>
            {argsPreview}
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        {/* TODO Phase ③: wire ▶ 実行 to an explicit execute endpoint so the backend can
            skip execution when confidence < 0.5 and wait for user confirmation here. */}
        <button
          type="button"
          disabled
          style={{
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            padding: "4px 12px",
            fontSize: 13,
            background: "var(--bg-soft)",
            color: "var(--text-tertiary)",
            cursor: "not-allowed",
            opacity: 0.6,
          }}
        >
          ▶ {t("ask.lowconf.run")}
        </button>
        <button
          type="button"
          onClick={onEdit}
          style={{
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            padding: "4px 12px",
            fontSize: 13,
            background: "var(--bg-surface)",
            color: "var(--accent)",
            cursor: "pointer",
          }}
        >
          ✎ {t("ask.lowconf.edit")}
        </button>
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
  const joined = bits.join(" ・ "); // i18n-ignore: separator
  return (
    <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: -4, marginBottom: 8 }}>
      {joined}
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
    const serviceTypeIdx = cols.findIndex((c) => c === "service_type");
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
                    {t(`ask.col.${c}`, { defaultValue: c })}
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
                        : j === serviceTypeIdx && cell != null
                          ? t(`common.service_value.${String(cell)}`, { defaultValue: String(cell) })
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
