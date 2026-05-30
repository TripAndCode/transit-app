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
  useDashboardHeatmap,
  useDashboardAnomalies,
  useDashboardMovers,
} from "../api/hooks";
import { useRangeContext, DEFAULT_RANGE_DAYS, isoDaysAgo, todayISO } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ConvMessage, FilterCtx } from "../api/types";
import type { ToolResult, TrendDay } from "../api/types";
import { ThreadSidebar } from "../components/ThreadSidebar";
import { FilterContextBar } from "../components/FilterContextBar";
import { DailyChart } from "../components/charts/DailyChart";
import { DelayHeatmap } from "../components/DelayHeatmap";
import { AnomalyTimeline } from "../components/AnomalyTimeline";
import { MoversList } from "../components/MoversList";
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

  // Heatmap dimension toggle — parent owns state so filter changes can reset it
  const [heatDim, setHeatDim] = useState<"dow" | "hour_band">("dow");

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

  // When the active conversation loads, sync the filter context
  useEffect(() => {
    const fc = convQuery.data?.conversation?.filter_ctx;
    if (fc) setFilterCtx(resolvedFilterCtx(fc));
  }, [convQuery.data?.conversation?.filter_ctx]);

  // When no thread is active, use URL range context
  useEffect(() => {
    if (!activeId) setFilterCtx(rangeCtxToFilterCtx(rangeCtx));
  }, [activeId, rangeCtx]);

  // ── Dashboard queries ─────────────────────────────────────────────────────
  const heatmap = useDashboardHeatmap(id ?? 0, filterCtx, heatDim, 15);
  const anomaly = useDashboardAnomalies(id ?? 0, filterCtx, 2.0);
  const movers = useDashboardMovers(id ?? 0, filterCtx, 7, 8);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [convQuery.data?.messages, appendMsg.isPending]);

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
            onChange={setFilterCtx}
            pending={appendMsg.isPending || createConv.isPending}
          />
        </div>

        {/* Scrollable content area */}
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
          {/* ── Dashboard row ───────────────────────────────────────────── */}
          {id != null && (
            <div style={{ marginBottom: 16 }}>
              {/* Heatmap + Anomaly side-by-side on md+, stacked on narrow */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: 16,
                  marginBottom: 16,
                }}
              >
                <DelayHeatmap
                  data={heatmap.data}
                  isLoading={heatmap.isLoading}
                  isError={heatmap.isError}
                  dimension={heatDim}
                  onDimensionChange={setHeatDim}
                  onCellClick={(rc, d, v) =>
                    console.log("heatmap click", rc, d, v)
                  }
                />
                <AnomalyTimeline
                  data={anomaly.data}
                  isLoading={anomaly.isLoading}
                  isError={anomaly.isError}
                  onAnomalyClick={(d, s) =>
                    console.log("anomaly click", d, s)
                  }
                />
              </div>

              <MoversList
                data={movers.data}
                isLoading={movers.isLoading}
                isError={movers.isError}
                windowDays={7}
                onRowClick={(rc) => console.log("mover click", rc)}
              />
            </div>
          )}

          {/* ── Question cards row ──────────────────────────────────────── */}
          {id != null && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                gap: 12,
                marginBottom: 16,
              }}
            >
              {templates.map((tpl) => (
                <ParameterizedQuestionCard
                  key={tpl.id}
                  template={tpl}
                  agencyId={id}
                  filterCtx={filterCtx}
                  busy={appendMsg.isPending || createConv.isPending}
                  onSubmit={handleCardSubmit}
                />
              ))}
            </div>
          )}

          {/* ── Thread messages ─────────────────────────────────────────── */}
          {hasMessages && (
            <>
              <MessageList
                messages={messages}
                formatRoute={routeNames.format}
                t={t}
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
        justifyContent: isUser ? "flex-end" : "flex-start",
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
