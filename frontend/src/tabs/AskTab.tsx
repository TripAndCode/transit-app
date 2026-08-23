/**
 * AskTab — conversational analytics interface for an agency.
 *
 * Manages thread selection, filter context, message dispatch, and scroll
 * behaviour for the Ask feature. Renders a two-column layout: {@link ThreadSidebar}
 * on the left, and a scrollable message list with a sticky {@link QuestionDock}
 * on the right. Handles anonymous-to-authenticated conversation migration on
 * first login.
 *
 * Message rendering lives in ./ask/ (MessageList, RichResult, FollowupChipsRow).
 */
import { useState, useRef, useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
import { useRangeContext } from "../api/rangeContext";
import { useRouteNames } from "../api/useRouteNames";
import { conversationsAnon } from "../api/conversationsAnon";
import type { FilterCtx } from "../api/types";
import { ThreadSidebar } from "../components/ThreadSidebar";
import { FilterContextBar } from "../components/FilterContextBar";
import { QuestionDock } from "../components/QuestionDock";
import { buildCardTemplates, defaultsFor, type CardTemplate } from "../components/askCardTemplates";
import { Spinner } from "../components/Spinner";
import { Skeleton } from "../components/Skeleton";
import { rangeCtxToFilterCtx, resolvedFilterCtx } from "./ask/filterCtx";
import { MessageList } from "./ask/MessageList";
import { FollowupChipsRow } from "./ask/FollowupChipsRow";
import { AskLandingCards } from "./ask/AskLandingCards";

export function AskTab() {
  const { t, i18n } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [rangeCtx] = useRangeContext();
  const routeNames = useRouteNames(id);

  // ── Thread state ──────────────────────────────────────────────────────────
  const [activeId, setActiveId] = useState<string | null>(null);

  // ── Ask-dock composing state (lifted from QuestionDock so a landing-area
  //    suggestion pill can open a specific chip, not just the bottom dock's
  //    own toolbar) ───────────────────────────────────────────────────────
  const [composingId, setComposingId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  // Free-text follow-up draft, lifted so it survives FollowupChipsRow's
  // unmount while a follow-up request is in flight (row is hidden during
  // followup.isPending below).
  const [followupDraft, setFollowupDraft] = useState("");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const templates = useMemo(() => buildCardTemplates(), [i18n.language]);

  // ── Hooks ─────────────────────────────────────────────────────────────────
  const authed = useIsAuthenticated();
  const migrateAnon = useMigrateAnon(id ?? 0);
  const migratedRef = useRef(false);

  // Anon → authed migration: fire once when an authenticated user actually has
  // local threads to import. Gating on the local count (not just a ref) means a
  // remount on agency switch can't re-fire it once localStorage has been cleared.
  useEffect(() => {
    if (authed && !migratedRef.current && id != null && conversationsAnon.exportAll().length > 0) {
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

  // ── Filter context (derived, no sync effects) ─────────────────────────────
  // Single source of truth, in priority order:
  //   1. an unsaved user edit for the *current* thread (keyed by activeId so a
  //      thread switch never carries another thread's edit across),
  //   2. the active conversation's stored filter_ctx,
  //   3. the URL range context (no active thread / conversation still loading).
  const [filterEdit, setFilterEdit] = useState<{ key: string | null; fc: FilterCtx } | null>(null);
  const storedFc = convQuery.data?.conversation?.filter_ctx;
  const filterCtx: FilterCtx =
    filterEdit && filterEdit.key === activeId
      ? filterEdit.fc
      : activeId && storedFc
        ? resolvedFilterCtx(storedFc)
        : rangeCtxToFilterCtx(rangeCtx);

  // User-initiated filter edit. When an active thread exists, persist the new
  // filter to the conversation so subsequent 実行 calls dispatch with the
  // visible filter. The save promise is tracked so handleCardSubmit can await
  // it — without that, an edit followed by an immediate 実行 raced the PATCH
  // and the backend answered with the previous (stale) filter scope.
  const pendingFilterSave = useRef<Promise<unknown> | null>(null);
  function handleFilterChange(next: FilterCtx) {
    setFilterEdit({ key: activeId, fc: next });
    if (activeId) {
      pendingFilterSave.current = updateConv
        .mutateAsync({ id: activeId, patch: { filter_ctx: next } })
        .finally(() => {
          pendingFilterSave.current = null;
        });
    }
  }

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [convQuery.data?.messages]);

  // ── Event handlers ────────────────────────────────────────────────────────

  function handleSelectThread(threadId: string | null) {
    setActiveId(threadId);
    setFilterEdit(null);
    setFollowupDraft("");
    followup.reset();
  }

  function handleNewThread() {
    setActiveId(null);
    setFilterEdit(null);
    setFollowupDraft("");
    followup.reset();
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

    // An in-flight filter save must land before dispatch — the authed path's
    // backend reads the *persisted* conversation.filter_ctx.
    if (pendingFilterSave.current) await pendingFilterSave.current;

    let convId = activeId;
    if (convId === null) {
      const created = await createConv.mutateAsync({
        title: user_summary.slice(0, 60),
        filter_ctx: filterCtx,
      });
      convId = created.conversation_id;
      setActiveId(convId);
      // The new conversation was created with the visible filter; drop any
      // no-thread edit so the derived ctx now reads from the conversation.
      setFilterEdit(null);
    }

    appendMsg.mutate({ conversationId: convId, tool, args, user_summary, filter_ctx: filterCtx });
  }

  const busy = appendMsg.isPending || createConv.isPending;

  function handleChipTap(tpl: CardTemplate) {
    if (busy) return;
    if (composingId === tpl.id) {
      setComposingId(null);
      setValues({});
      return;
    }
    setComposingId(tpl.id);
    setValues(defaultsFor(tpl));
  }

  function handleValueChange(name: string, next: unknown) {
    setValues((prev) => ({ ...prev, [name]: next }));
  }

  function handleRunComplete() {
    setComposingId(null);
    setValues({});
  }

  function handleInstantSubmit(tpl: CardTemplate) {
    if (busy) return;
    const args = { ...tpl.fixed_args, ...defaultsFor(tpl) };
    handleCardSubmit({ tool: tpl.tool, args, user_summary: tpl.buildSummary(defaultsFor(tpl), t) });
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
                  onFollowup={(ctxMsgId, question) => {
                    if (!activeId) return;
                    // Only clear the draft if this submission *was* the draft --
                    // a canned chip prompt shouldn't wipe text the user is
                    // still composing.
                    const isDraftSubmit = question === followupDraft.trim();
                    followup.mutate(
                      { conversationId: activeId, contextMessageId: ctxMsgId, question },
                      { onSuccess: () => isDraftSubmit && setFollowupDraft("") },
                    );
                  }}
                  draftValue={followupDraft}
                  onDraftChange={setFollowupDraft}
                  error={followup.isError ? followup.error : null}
                  maxChars={followupFlag.data?.max_question_chars}
                />
              )}
            </>
          ) : (
            <AskLandingCards
              templates={templates}
              onInstantSubmit={handleInstantSubmit}
              onOpenChip={handleChipTap}
              busy={busy}
            />
          )}
        </div>

        {/* Bottom dock */}
        {id != null && (
          <QuestionDock
            agencyId={id}
            busy={busy}
            onSubmit={handleCardSubmit}
            composingId={composingId}
            values={values}
            onChipTap={handleChipTap}
            onValueChange={handleValueChange}
            showToolbar={hasMessages}
            onRunComplete={handleRunComplete}
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
