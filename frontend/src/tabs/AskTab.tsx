import { useState, useRef, useEffect, useMemo } from "react";
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
import { ParameterizedQuestionCard } from "../components/ParameterizedQuestionCard";
import { buildCardTemplates } from "../components/askCardTemplates";

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
  const { t, i18n } = useTranslation();
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
  const followup = useFollowup(id ?? 0);
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
  }, [convQuery.data?.messages, appendMsg.isPending]);

  // Cards default to expanded on empty thread, collapsed once messages exist.
  // User-controllable via the strip toggle to keep "ask another" one tap away.
  const hasMessagesNow = (convQuery.data?.messages?.length ?? 0) > 0;
  const [cardsExpanded, setCardsExpanded] = useState<boolean>(!hasMessagesNow);
  useEffect(() => {
    setCardsExpanded(!hasMessagesNow);
  }, [activeId, hasMessagesNow]);

  // ── Card templates ────────────────────────────────────────────────────────
  // Templates are locale-independent (buildSummary receives t at call-site).
  // We still depend on i18n.language so the memo key updates on locale switch
  // (components re-render with fresh t anyway, but this keeps the dep array honest).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const templates = useMemo(() => buildCardTemplates(), [i18n.language]);

  // ── Event handlers ────────────────────────────────────────────────────────

  function handleSelectThread(id: string | null) {
    setActiveId(id);
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

    appendMsg.mutate({ conversationId: convId, tool, args, user_summary });
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

        {/* ── Cards (lifted out of scroll area so they're always reachable) ── */}
        {id != null && (
          <div
            style={{
              flexShrink: 0,
              padding: "12px 16px 0",
              borderBottom: hasMessages ? "1px solid var(--border-soft)" : undefined,
            }}
          >
            {cardsExpanded ? (
              <>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                    gap: 12,
                    marginBottom: hasMessages ? 8 : 16,
                  }}
                >
                  {templates.map((tpl) => (
                    <ParameterizedQuestionCard
                      key={tpl.id}
                      template={tpl}
                      agencyId={id}
                      filterCtx={filterCtx}
                      busy={appendMsg.isPending || createConv.isPending}
                      onSubmit={(payload) => {
                        handleCardSubmit(payload);
                        setCardsExpanded(false);
                      }}
                    />
                  ))}
                </div>
                {hasMessages && (
                  <button
                    type="button"
                    onClick={() => setCardsExpanded(false)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "var(--text-tertiary)",
                      fontSize: 12,
                      cursor: "pointer",
                      marginBottom: 8,
                    }}
                  >
                    {t("ask.cards_collapse", { defaultValue: "▴ 質問パネルを閉じる" })}
                  </button>
                )}
              </>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 8,
                  alignItems: "center",
                  marginBottom: 8,
                }}
                role="toolbar"
                aria-label={t("ask.cards_strip_aria", { defaultValue: "新しい質問" })}
              >
                {templates.map((tpl) => (
                  <button
                    key={tpl.id}
                    type="button"
                    onClick={() => setCardsExpanded(true)}
                    title={t(tpl.title_key)}
                    style={{
                      background: "var(--bg-soft)",
                      border: "1px solid var(--border-soft)",
                      borderRadius: 999,
                      padding: "4px 12px",
                      fontSize: 13,
                      cursor: "pointer",
                      color: "var(--text-primary)",
                    }}
                  >
                    {tpl.emoji} {t(tpl.title_key)}
                  </button>
                ))}
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--text-tertiary)",
                    marginLeft: 4,
                  }}
                >
                  {t("ask.cards_strip_hint", {
                    defaultValue: "クリックで質問パネルを開く",
                  })}
                </span>
              </div>
            )}
          </div>
        )}

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
          {hasMessages ? (
            <>
              <MessageList
                messages={messages}
                formatRoute={routeNames.format}
                t={t}
                followupEnabled={followupEnabled}
                followupBusy={followup.isPending}
                onFollowup={(ctxMsgId, question) =>
                  activeId &&
                  followup.mutate({
                    conversationId: activeId,
                    contextMessageId: ctxMsgId,
                    question,
                  })
                }
              />

              {appendMsg.isPending && (
                <div
                  role="status"
                  aria-live="polite"
                  style={{ padding: 12, color: "var(--text-tertiary)", fontSize: 13 }}
                >
                  {t("ask.thinking")}
                </div>
              )}
            </>
          ) : (
            <div
              style={{
                color: "var(--text-tertiary)",
                fontSize: 13,
                padding: "8px 4px",
              }}
            >
              {t("ask.empty_hint", {
                defaultValue: "上の質問パネルから一つ選んで 実行 を押してください。",
              })}
            </div>
          )}
        </div>
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
  followupEnabled,
  followupBusy,
  onFollowup,
}: {
  messages: ConvMessage[];
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
  followupEnabled: boolean;
  followupBusy: boolean;
  onFollowup: (contextMsgId: number, question: string) => void;
}) {
  // Last assistant message with a tool result is the only one that gets a
  // follow-up input — follow-ups on follow-ups would compound LLM error.
  const lastResultMsgId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.tool && m.result) return m.message_id;
    }
    return null;
  })();

  return (
    <>
      {messages.map((m) => (
        <Bubble
          key={m.message_id}
          msg={m}
          formatRoute={formatRoute}
          t={t}
          showFollowupInput={
            followupEnabled && m.message_id === lastResultMsgId
          }
          followupBusy={followupBusy}
          onFollowup={onFollowup}
        />
      ))}
    </>
  );
}

// ─── Bubble ───────────────────────────────────────────────────────────────────

function Bubble({
  msg,
  formatRoute,
  t,
  showFollowupInput,
  followupBusy,
  onFollowup,
}: {
  msg: ConvMessage;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
  showFollowupInput: boolean;
  followupBusy: boolean;
  onFollowup: (contextMsgId: number, question: string) => void;
}) {
  const isUser = msg.role === "user";
  const result = msg.result as ToolResult | null;
  const wide = !isUser && (result?.kind === "table" || result?.kind === "series");
  const [followupText, setFollowupText] = useState("");

  function submitFollowup() {
    const q = followupText.trim();
    if (!q || followupBusy) return;
    onFollowup(msg.message_id, q);
    setFollowupText("");
  }

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

      {showFollowupInput && (
        <div
          style={{
            marginTop: 6,
            width: wide ? "100%" : "85%",
            display: "flex",
            gap: 6,
          }}
        >
          <input
            type="text"
            value={followupText}
            onChange={(e) => setFollowupText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submitFollowup();
              }
            }}
            disabled={followupBusy}
            placeholder={t("ask.followup_placeholder", {
              defaultValue: "この結果について質問...",
            })}
            maxLength={500}
            style={{
              flex: 1,
              padding: "6px 10px",
              fontSize: 13,
              border: "1px solid var(--border-soft)",
              borderRadius: 6,
              background: "var(--bg-surface)",
              color: "var(--text-primary)",
            }}
          />
          <button
            type="button"
            onClick={submitFollowup}
            disabled={!followupText.trim() || followupBusy}
            style={{
              padding: "6px 12px",
              fontSize: 13,
              background: "var(--accent, #4a8aaa)",
              color: "white",
              border: "none",
              borderRadius: 6,
              cursor: followupText.trim() && !followupBusy ? "pointer" : "not-allowed",
              opacity: followupText.trim() && !followupBusy ? 1 : 0.6,
            }}
          >
            {followupBusy
              ? t("ask.thinking", { defaultValue: "..." })
              : t("ask.followup_send", { defaultValue: "送信" })}
          </button>
        </div>
      )}
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
