/**
 * AskTab — conversational analytics interface for an agency.
 *
 * Manages thread selection, filter context, message dispatch, and scroll
 * behaviour for the Ask feature. Renders a two-column layout: {@link ThreadSidebar}
 * on the left, and a scrollable message list with a sticky {@link QuestionDock}
 * on the right. Handles anonymous-to-authenticated conversation migration on
 * first login.
 */
import { useState, useRef, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  useConversation,
  useCreateConversation,
  useAppendMessage,
  useMigrateAnon,
  useIsAuthenticated,
  useUpdateConversation,
  useFollowup,
  useFollowupEnabled,
} from "../api/hooks";
import { useRangeContext, DEFAULT_RANGE_DAYS, isoDaysAgo, todayISO } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ConvMessage, FilterCtx } from "../api/types";
import type { ToolResult, TrendDay } from "../api/types";
import { ThreadSidebar } from "../components/ThreadSidebar";
import { FilterContextBar } from "../components/FilterContextBar";
import { DailyChart } from "../components/charts/DailyChart";
import { QuestionDock } from "../components/QuestionDock";
import { FOLLOWUP_CHIPS } from "../components/askFollowupChips";
import { Spinner } from "../components/Spinner";
import { Skeleton } from "../components/Skeleton";

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Convert URL-based RangeCtx to FilterCtx for new thread seeding. */
function rangeCtxToFilterCtx(ctx: ReturnType<typeof useRangeContext>[0]): FilterCtx {
  return {
    from_date: ctx.from,
    to_date: ctx.to,
    dow: ctx.dow !== "all" ? ctx.dow : undefined,
    time_band: ctx.time_band !== "all" ? ctx.time_band : undefined,
    service: ctx.service !== "all" ? ctx.service : undefined,
    routes: ctx.routes.length > 0 ? ctx.routes : undefined,
  };
}

/** Derive a FilterCtx from a conversation's stored filter_ctx, with defaults. */
function resolvedFilterCtx(fc: FilterCtx | undefined | null): FilterCtx {
  const today = todayISO();
  const fromDefault = isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
  return {
    from_date: fc?.from_date ?? fromDefault,
    to_date: fc?.to_date ?? today,
    dow: fc?.dow ?? "all",
    time_band: fc?.time_band ?? "all",
    service: fc?.service ?? "all",
    routes: fc?.routes ?? [],
  };
}

// ─── AskTab ───────────────────────────────────────────────────────────────────

export function AskTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [rangeCtx] = useRangeContext();
  const routeNames = useRouteNames(id);

  // ── Thread state ──────────────────────────────────────────────────────────
  const [activeId, setActiveId] = useState<string | null>(null);

  // Local FilterCtx for the current view (synced from the active conversation's filter_ctx)
  const [filterCtx, setFilterCtx] = useState<FilterCtx>(() => rangeCtxToFilterCtx(rangeCtx));

  // ── Hooks ─────────────────────────────────────────────────────────────────
  const authed = useIsAuthenticated();
  const migrateAnon = useMigrateAnon(id ?? 0);
  const migratedRef = useRef(false);

  // Anon → authed migration: fire once when user first becomes authenticated
  useEffect(() => {
    if (authed && !migratedRef.current && id != null) {
      migratedRef.current = true;
      migrateAnon.mutate();
    }
  }, [authed, id]); // eslint-disable-line react-hooks/exhaustive-deps

  const convQuery = useConversation(id ?? 0, activeId);
  const createConv = useCreateConversation(id ?? 0);
  const appendMsg = useAppendMessage(id ?? 0);
  const updateConv = useUpdateConversation(id ?? 0);
  const followup = useFollowup(id ?? 0, authed);
  const followupFlag = useFollowupEnabled(id);
  const followupEnabled = followupFlag.data?.enabled === true;

  // When the active conversation loads, sync the filter context
  useEffect(() => {
    const fc = convQuery.data?.conversation?.filter_ctx;
    if (fc) setFilterCtx(resolvedFilterCtx(fc));
  }, [convQuery.data?.conversation?.filter_ctx]);

  // When no thread is active, use URL range context
  useEffect(() => {
    if (!activeId) setFilterCtx(rangeCtxToFilterCtx(rangeCtx));
  }, [activeId, rangeCtx]);

  // User-initiated filter edit. When an active thread exists, persist the
  // new filter to the conversation so subsequent 実行 calls dispatch with
  // the visible filter — otherwise the backend reads the stale conv.filter_ctx
  // and the UI lies about scope.
  function handleFilterChange(next: FilterCtx) {
    setFilterCtx(next);
    if (activeId) {
      updateConv.mutate({ id: activeId, patch: { filter_ctx: next } });
    }
  }

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [convQuery.data?.messages]);

  // ── Event handlers ────────────────────────────────────────────────────────

  function handleSelectThread(threadId: string | null) {
    setActiveId(threadId);
  }

  function handleNewThread() {
    setActiveId(null);
    setFilterCtx(rangeCtxToFilterCtx(rangeCtx));
  }

  async function handleCardSubmit({
    tool,
    args,
    user_summary,
  }: {
    tool: string;
    args: Record<string, unknown>;
    user_summary: string;
  }) {
    if (id == null) return;

    // Coerce best_first string "true"/"false" → boolean
    if (typeof args.best_first === "string") {
      args = { ...args, best_first: args.best_first === "true" };
    }

    let convId = activeId;
    if (convId === null) {
      const created = await createConv.mutateAsync({
        title: user_summary.slice(0, 60),
        filter_ctx: filterCtx,
      });
      convId = created.conversation_id;
      setActiveId(convId);
    }

    appendMsg.mutate({ conversationId: convId, tool, args, user_summary, filter_ctx: filterCtx });
  }

  // ── Derived state ─────────────────────────────────────────────────────────

  const messages = convQuery.data?.messages ?? [];
  const hasMessages = messages.length > 0;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "240px 1fr",
        height: "100%",
        minHeight: 0,
      }}
      className="ask-tab-grid"
    >
      {/* ── Sidebar ──────────────────────────────────────────────────────── */}
      {id != null && (
        <ThreadSidebar
          agencyId={id}
          activeId={activeId}
          onSelect={handleSelectThread}
          onNewThread={handleNewThread}
        />
      )}

      {/* ── Main area ────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        {/* Filter bar */}
        <div style={{ padding: "8px 16px 0", flexShrink: 0 }}>
          <FilterContextBar
            value={filterCtx}
            onChange={handleFilterChange}
            pending={appendMsg.isPending || createConv.isPending}
          />
        </div>

        {/* ── Scrollable thread area ─────────────────────────────────────── */}
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            padding: "12px 16px",
            scrollbarGutter: "stable",
          }}
        >
          {activeId !== null && convQuery.isPending ? (
            // Thread selected but its messages haven't loaded yet — show a
            // skeleton instead of the empty-state hint, which would otherwise
            // read 'start a new conversation' while a saved thread is still
            // fetching (review-flagged regression: R5 P1).
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Skeleton height={64} />
              <Skeleton height={120} />
              <Skeleton height={64} style={{ alignSelf: "flex-end", width: "60%" }} />
            </div>
          ) : hasMessages ? (
            <>
              <MessageList
                messages={messages}
                formatRoute={routeNames.format}
                t={t}
              />

              {(appendMsg.isPending || followup.isPending) && (
                <div
                  role="status"
                  aria-live="polite"
                  style={{
                    padding: 12,
                    color: "var(--text-tertiary)",
                    fontSize: 13,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <Spinner size={14} />
                  {t("ask.thinking")}
                </div>
              )}

              {/* Follow-up chips pinned after the latest message so the
                  conversation can continue indefinitely. Each follow-up is
                  grounded on the most recent tool result (not on prior LLM
                  answers) to avoid compounding LLM errors. */}
              {followupEnabled && !followup.isPending && !appendMsg.isPending && (
                <FollowupChipsRow
                  messages={messages}
                  t={t}
                  onFollowup={(ctxMsgId, question) =>
                    activeId &&
                    followup.mutate({
                      conversationId: activeId,
                      contextMessageId: ctxMsgId,
                      question,
                    })
                  }
                />
              )}
            </>
          ) : (
            <div
              style={{
                color: "var(--text-tertiary)",
                fontSize: 13,
                padding: "8px 4px",
                textAlign: "center",
              }}
            >
              {t("ask.dock.empty_hint")}
            </div>
          )}
        </div>

        {/* Bottom dock */}
        {id != null && (
          <QuestionDock
            agencyId={id}
            busy={appendMsg.isPending || createConv.isPending}
            onSubmit={handleCardSubmit}
          />
        )}
      </div>

      {/* Responsive CSS for the two-column grid */}
      <style>{`
        @media (max-width: 640px) {
          .ask-tab-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}

// ─── MessageList ──────────────────────────────────────────────────────────────

function MessageList({
  messages,
  formatRoute,
  t,
}: {
  messages: ConvMessage[];
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  return (
    <>
      {messages.map((m) => (
        <Bubble key={m.message_id} msg={m} formatRoute={formatRoute} t={t} />
      ))}
    </>
  );
}

// ─── FollowupChipsRow ─────────────────────────────────────────────────────────

/** Bottom-of-thread follow-up chips. Grounds every follow-up on the most
 *  recent assistant message that carries a tool result, so multi-turn
 *  follow-ups never compound LLM-generated answers. Hidden when the thread
 *  has no tool result to ground on. */
function FollowupChipsRow({
  messages,
  t,
  onFollowup,
}: {
  messages: ConvMessage[];
  t: TFunction;
  onFollowup: (contextMsgId: number, question: string) => void;
}) {
  const lastResultMsgId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.tool && m.result) return m.message_id;
    }
    return null;
  })();
  if (lastResultMsgId == null) return null;

  return (
    <div
      role="group"
      aria-label={t("ask.followup_chips.panel_aria", { defaultValue: "フォローアップ質問" })}
      style={{
        marginTop: 8,
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
      }}
    >
      {FOLLOWUP_CHIPS.map((chip) => (
        <button
          key={chip.id}
          type="button"
          onClick={() => onFollowup(lastResultMsgId, t(chip.prompt_key))}
          style={{
            padding: "5px 12px",
            fontSize: 12,
            background: "var(--bg-soft, #f4f4f5)",
            color: "var(--text-secondary, #52525b)",
            border: "1px solid var(--border-soft, #e4e4e7)",
            borderRadius: 999,
            cursor: "pointer",
            whiteSpace: "nowrap",
            transition: "background 0.15s",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft-hover, #e4e4e7)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft, #f4f4f5)";
          }}
        >
          {t(chip.label_key)}
        </button>
      ))}
    </div>
  );
}

// ─── Bubble ───────────────────────────────────────────────────────────────────

function Bubble({
  msg,
  formatRoute,
  t,
}: {
  msg: ConvMessage;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  const isUser = msg.role === "user";
  const result = msg.result as ToolResult | null;
  const wide = !isUser && (result?.kind === "table" || result?.kind === "series");

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        margin: "12px 0",
      }}
    >
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
          <RichResult result={result} fallbackText={msg.rendered_summary ?? ""} formatRoute={formatRoute} t={t} />
        ) : (
          <span style={{ whiteSpace: "pre-wrap" }}>{msg.rendered_summary ?? msg.tool}</span>
        )}
        {!isUser && (msg.tool || msg.result) && (
          <details style={{ marginTop: 8, color: "var(--text-tertiary)", fontSize: 12 }}>
            <summary style={{ cursor: "pointer" }}>{t("common.details")}</summary>
            <pre style={{ overflowX: "auto", marginTop: 6, whiteSpace: "pre" }}>
              {JSON.stringify({ tool: msg.tool, args: msg.args, result: msg.result }, null, 2)}
            </pre>
          </details>
        )}
      </div>

    </div>
  );
}

// ─── RichResult ───────────────────────────────────────────────────────────────

function RichResult({
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
