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
} from "../api/hooks";
import { useRangeContext, DEFAULT_RANGE_DAYS, isoDaysAgo, todayISO } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import type { ConvMessage, ChipTemplate, FollowupChip, FilterCtx } from "../api/types";
import type { ToolResult, TrendDay } from "../api/types";
import { ThreadSidebar } from "../components/ThreadSidebar";
import { FilterContextBar } from "../components/FilterContextBar";
import { ChipCatalog } from "../components/ChipCatalog";
import { FollowupChips } from "../components/FollowupChips";
import { AskBuildForm } from "../components/AskBuildForm";
import { DailyChart } from "../components/charts/DailyChart";

// ─── types ────────────────────────────────────────────────────────────────────

type BuildState = {
  chip?: ChipTemplate;
  existingTool?: string;
  existingArgs?: Record<string, unknown>;
} | null;

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
  const [buildOpen, setBuildOpen] = useState<BuildState>(null);
  const [catalogVisible, setCatalogVisible] = useState(false);
  // Staged chip: tap-and-confirm pattern. A tapped chip is highlighted +
  // shown in a confirmation banner; nothing is committed until the user
  // clicks 実行. This stops the "instant thread creation" surprise.
  const [stagedChip, setStagedChip] = useState<ChipTemplate | null>(null);

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

  // When the active conversation loads, sync the filter context
  useEffect(() => {
    const fc = convQuery.data?.conversation?.filter_ctx;
    if (fc) setFilterCtx(resolvedFilterCtx(fc));
  }, [convQuery.data?.conversation?.filter_ctx]);

  // When no thread is active, use URL range context
  useEffect(() => {
    if (!activeId) setFilterCtx(rangeCtxToFilterCtx(rangeCtx));
  }, [activeId, rangeCtx]);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [convQuery.data?.messages, appendMsg.isPending]);

  // ── Event handlers ────────────────────────────────────────────────────────

  function handleSelectThread(id: string | null) {
    setActiveId(id);
    setBuildOpen(null);
    setCatalogVisible(false);
  }

  function handleNewThread() {
    setActiveId(null);
    setBuildOpen(null);
    setCatalogVisible(false);
    setFilterCtx(rangeCtxToFilterCtx(rangeCtx));
  }

  function handleChipSelect(chip: ChipTemplate) {
    if (id == null) return;
    if (chip.builder_required) {
      setBuildOpen({ chip });
      setStagedChip(null);
      return;
    }
    // Stage the chip — don't commit yet. The user sees a confirm banner
    // and can adjust the filter context or pick a different chip first.
    setStagedChip(chip);
  }

  async function handleStagedExecute() {
    if (id == null || stagedChip == null) return;
    const chip = stagedChip;
    let convId = activeId;
    if (convId === null) {
      const created = await createConv.mutateAsync({
        title: chip.title ?? chip.id,
        filter_ctx: filterCtx,
      });
      convId = created.conversation_id;
      setActiveId(convId);
    }
    setBuildOpen(null);
    setCatalogVisible(false);
    setStagedChip(null);
    appendMsg.mutate({ conversationId: convId, chip_id: chip.id });
  }

  function handleStagedCancel() {
    setStagedChip(null);
  }

  function handleOpenBuilder() {
    setBuildOpen({});
  }

  async function handleBuildSubmit(tool: string, args: Record<string, unknown>) {
    if (id == null) return;
    let convId = activeId;

    if (convId === null) {
      // Create new thread with a title derived from the tool name
      const created = await createConv.mutateAsync({
        title: `🛠 ${tool}`,
        filter_ctx: rangeCtxToFilterCtx(rangeCtx),
      });
      convId = created.conversation_id;
      setActiveId(convId);
    }

    setBuildOpen(null);
    appendMsg.mutate({ conversationId: convId, tool, args });
  }

  function handleBuildCancel() {
    setBuildOpen(null);
  }

  function handleFollowupPick(chip: FollowupChip) {
    if (id == null || activeId == null) return;
    appendMsg.mutate({ conversationId: activeId, tool: chip.tool, args: chip.args });
  }

  function handleFollowupOpenBuilder() {
    if (!convQuery.data?.messages) return;
    const msgs = convQuery.data.messages;
    const lastAsst = [...msgs].reverse().find((m) => m.role === "assistant");
    setBuildOpen({
      existingTool: lastAsst?.tool ?? undefined,
      existingArgs: lastAsst?.args ?? undefined,
    });
  }

  function handleBackToCatalog() {
    setCatalogVisible(true);
    setBuildOpen(null);
  }

  // ── Derived state ─────────────────────────────────────────────────────────

  const messages = convQuery.data?.messages ?? [];
  const lastAssistantMsg = [...messages].reverse().find((m) => m.role === "assistant") ?? null;
  const hasMessages = messages.length > 0;

  // Build form initialValue derived from buildOpen state
  // confidence=0 is used as a placeholder — AskBuildForm only pre-populates tool/args from it.
  const buildInitialValue =
    buildOpen?.chip != null
      ? { tool: buildOpen.chip.tool, args: buildOpen.chip.args, confidence: 0, rationale: null }
      : buildOpen?.existingTool != null
        ? { tool: buildOpen.existingTool, args: buildOpen.existingArgs ?? {}, confidence: 0, rationale: null }
        : null;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "240px 1fr",
        height: "100%",
        minHeight: 0,
        // On mobile (≤640px), collapse to single column — the sidebar handles its own hamburger
        // but we need the grid to be single-column so main area doesn't overflow.
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

        {/* Scrollable content area. ``scrollbarGutter: stable`` forces the scrollbar
            to reserve space (so the user can SEE there's a scrollable region even
            when not actively scrolling). */}
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
          {/* Build form — shown when buildOpen is not null */}
          {buildOpen !== null && id != null && (
            <div
              style={{
                marginBottom: 16,
                padding: "12px 16px",
                background: "var(--bg-surface)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-lg)",
              }}
            >
              <AskBuildForm
                agencyId={id}
                initialValue={buildInitialValue}
                onSubmit={handleBuildSubmit}
                onCancel={handleBuildCancel}
              />
            </div>
          )}

          {/* Staged-chip confirm banner. Shown when a chip has been tapped
              but not yet committed. The user can adjust the filter context
              above (期間・曜日・時間帯) then click 実行 to commit, or
              キャンセル to back out. */}
          {stagedChip !== null && (
            <div
              role="region"
              aria-label={t("ask.staged.region")}
              style={{
                marginBottom: 16,
                padding: "12px 14px",
                background: "rgba(74, 138, 170, 0.10)",
                border: "1px solid var(--accent, #4a8aaa)",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontWeight: 600 }}>{stagedChip.title}</span>
              <span style={{ flex: 1, fontSize: 12, opacity: 0.7 }}>
                {t("ask.staged.hint", { defaultValue: "条件を確認してから実行してください" })}
              </span>
              <button
                type="button"
                onClick={handleStagedExecute}
                disabled={appendMsg.isPending || createConv.isPending}
                style={{
                  background: "var(--accent, #4a8aaa)",
                  color: "white",
                  border: "none",
                  padding: "6px 14px",
                  borderRadius: 6,
                  cursor: appendMsg.isPending || createConv.isPending ? "wait" : "pointer",
                  fontWeight: 600,
                }}
              >
                ▶ {t("ask.staged.execute", { defaultValue: "実行" })}
              </button>
              <button
                type="button"
                onClick={handleStagedCancel}
                disabled={appendMsg.isPending || createConv.isPending}
                style={{
                  background: "transparent",
                  border: "1px solid var(--border-soft)",
                  padding: "6px 12px",
                  borderRadius: 6,
                  cursor: "pointer",
                }}
              >
                {t("ask.staged.cancel", { defaultValue: "キャンセル" })}
              </button>
            </div>
          )}

          {/* Empty thread state: show chip catalog (when no build form open) */}
          {!hasMessages && buildOpen === null && id != null && (
            <ChipCatalog
              agencyId={id}
              onSelect={handleChipSelect}
              onOpenBuilder={handleOpenBuilder}
              stagedChipId={stagedChip?.id ?? null}
            />
          )}

          {/* Filled thread state: message list */}
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

              {/* Inline catalog when user taps "カタログに戻る" */}
              {catalogVisible && id != null && (
                <div
                  style={{
                    marginTop: 16,
                    borderTop: "1px solid var(--border-soft)",
                    paddingTop: 16,
                  }}
                >
                  <ChipCatalog
                    agencyId={id}
                    onSelect={(chip) => { setCatalogVisible(false); handleChipSelect(chip); }}
                    onOpenBuilder={() => { setCatalogVisible(false); handleOpenBuilder(); }}
                    stagedChipId={stagedChip?.id ?? null}
                  />
                </div>
              )}

              {/* Follow-up chips after the last assistant message (only when no build open) */}
              {lastAssistantMsg && buildOpen === null && !catalogVisible && (
                <FollowupChips
                  message={lastAssistantMsg}
                  onPickFollowup={handleFollowupPick}
                  onOpenBuilder={handleFollowupOpenBuilder}
                  onBackToCatalog={handleBackToCatalog}
                />
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
