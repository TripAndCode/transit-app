import { useState, useRef, useEffect, useLayoutEffect, type CSSProperties, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useConversations, useUpdateConversation, useDeleteConversation } from "../api/hooks";
import type { Conversation, FilterCtx } from "../api/types";
import { rangeLabel } from "../utils/rangeLabel";
import { relativeTime } from "../utils/relativeTime";
import { isToday, isYesterday } from "../utils/threadDateBuckets";

// ─── helpers ─────────────────────────────────────────────────────────────────

function isThisWeek(iso: string): boolean {
  const d = new Date(iso).getTime();
  const now = Date.now();
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  return now - d < weekMs && d <= now;
}

function conversationScopeParts(
  conv: Conversation,
  t: (key: string, opts?: Record<string, unknown>) => string
): string[] {
  return [...(conv.filter_ctx.routes ?? []), filterSummary(conv.filter_ctx, t)];
}

function filterSummary(fc: FilterCtx, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const parts: string[] = [];

  // Date range
  const range = rangeLabel(fc, t);
  if (range) parts.push(range);

  // Day-of-week
  if (fc.dow && fc.dow !== "all") {
    const dowKey = fc.dow === "weekday" ? "filters.dow.weekday" : "filters.dow.weekend";
    parts.push(t(dowKey));
  }

  // Time band
  if (fc.time_band && fc.time_band !== "all") {
    const tbKey = `filters.time_band.${fc.time_band}`;
    const label = t(tbKey);
    if (label !== tbKey) parts.push(label);
  }

  return parts.join(" ・ "); // i18n-ignore: locale-neutral separator
}

// ─── context menu ────────────────────────────────────────────────────────────

type MenuState = {
  convId: string;
  x: number;
  y: number;
};

// ─── main component ──────────────────────────────────────────────────────────

type Props = {
  agencyId: number;
  activeId: string | null;
  onSelect: (conversationId: string | null) => void;
  onNewThread: () => void;
};

export function ThreadSidebar({ agencyId, activeId, onSelect, onNewThread }: Props) {
  const { t } = useTranslation();
  const { data: conversations = [], isLoading } = useConversations(agencyId);
  const updateConv = useUpdateConversation(agencyId);
  const deleteConv = useDeleteConversation(agencyId);

  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menu) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenu(null);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [menu]);

  // Close menu on Escape.
  useEffect(() => {
    if (!menu) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setMenu(null);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [menu]);

  // Clamp the menu to the viewport once its real size is known -- its
  // anchor point (the kebab button's own position) can put a wide/tall menu
  // partway or fully off-screen for rows near the right or bottom edge.
  // Written directly to the node's style (not React state) since this is a
  // one-off post-layout measurement of an external system (the rendered
  // menu's own box), not state to synchronize back into a render.
  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!menu || !node) return;
    const margin = 8;
    const left = Math.max(margin, Math.min(menu.x, window.innerWidth - node.offsetWidth - margin));
    const top = Math.max(margin, Math.min(menu.y, window.innerHeight - node.offsetHeight - margin));
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  }, [menu]);

  // Focus rename input when opened
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function openMenu(e: any, convId: string) {
    e.preventDefault();
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ convId, x: rect.right, y: rect.top });
  }

  function handleRename(conv: Conversation) {
    setMenu(null);
    setRenamingId(conv.conversation_id);
    setRenameValue(conv.title);
  }

  function commitRename(convId: string) {
    const trimmed = renameValue.trim();
    if (trimmed) {
      updateConv.mutate({ id: convId, patch: { title: trimmed } });
    }
    setRenamingId(null);
    setRenameValue("");
  }

  function handleTogglePin(conv: Conversation) {
    setMenu(null);
    updateConv.mutate({ id: conv.conversation_id, patch: { pinned: !conv.pinned } });
  }

  function handleDelete(conv: Conversation) {
    setMenu(null);
    if (window.confirm(t("ask.sidebar.delete_confirm"))) {
      deleteConv.mutate(conv.conversation_id);
      if (activeId === conv.conversation_id) onSelect(null);
    }
  }

  // Filter by the search query, then group the surviving conversations
  const query = search.normalize("NFKC").trim().toLocaleLowerCase();
  const matching = conversations.filter((c) =>
    [c.title, ...conversationScopeParts(c, t)]
      .join(" ").normalize("NFKC").toLocaleLowerCase().includes(query),
  );
  const pinned = matching.filter((c) => c.pinned);
  const unpinned = matching.filter((c) => !c.pinned);

  const todayList = unpinned.filter((c) => isToday(c.updated_at));
  const yesterdayList = unpinned.filter((c) => isYesterday(c.updated_at));
  const thisWeekList = unpinned.filter(
    (c) => !isToday(c.updated_at) && !isYesterday(c.updated_at) && isThisWeek(c.updated_at)
  );
  const earlierList = unpinned.filter(
    (c) => !isToday(c.updated_at) && !isYesterday(c.updated_at) && !isThisWeek(c.updated_at)
  );

  const groups: { labelKey: string; emoji?: string; items: Conversation[] }[] = [
    { labelKey: "ask.sidebar.pinned", emoji: "📌 ", items: pinned },
    { labelKey: "ask.sidebar.today", items: todayList },
    { labelKey: "ask.sidebar.yesterday", items: yesterdayList },
    { labelKey: "ask.sidebar.this_week", items: thisWeekList },
    { labelKey: "ask.sidebar.earlier", items: earlierList },
  ];

  const activeConv = menu ? conversations.find((c) => c.conversation_id === menu.convId) : null;

  // ── sidebar content ──────────────────────────────────────────────────────
  const sidebarContent = (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflowY: "auto",
        background: "var(--bg-surface)",
      }}
    >
      {/* New thread button */}
      <div style={{ padding: "var(--space-3)" }}>
        <button
          type="button"
          onClick={onNewThread}
          style={{
            width: "100%",
            background: "var(--accent)",
            color: "#fff",
            border: "none",
            borderRadius: "var(--radius)",
            padding: "9px var(--space-3)",
            fontSize: 14,
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            justifyContent: "center",
            transition: "opacity var(--transition)",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.85"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>＋</span>
          {t("ask.sidebar.new_thread")}
        </button>
      </div>

      {/* Thread list */}
      <div style={{ padding: "0 var(--space-3) var(--space-3)" }}>
        <input
          type="search"
          aria-label={t("ask.sidebar.search")}
          placeholder={t("ask.sidebar.search")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          style={{ width: "100%", boxSizing: "border-box", padding: 8 }}
        />
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "0 0 var(--space-3) 0" }}>
        {!isLoading && conversations.length > 0 && matching.length === 0 && (
          <p role="status" style={{ padding: "0 var(--space-3)", fontSize: 13 }}>
            {t("ask.sidebar.no_matches")}
          </p>
        )}
        {isLoading && (
          <div style={{ padding: "var(--space-3) var(--space-4)", color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("common.loading")}
          </div>
        )}

        {!isLoading && conversations.length === 0 && (
          <div style={{ padding: "var(--space-3) var(--space-4)", color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("ask.sidebar.empty")}
          </div>
        )}

        {/* Pinned, then date-grouped */}
        {groups.map(({ labelKey, emoji, items }) =>
          items.length === 0 ? null : (
            <section key={labelKey}>
              <div style={groupHeaderStyle}>{emoji}{t(labelKey)}</div>
              {items.map((conv) => (
                <ConvItem
                  key={conv.conversation_id}
                  conv={conv}
                  isActive={conv.conversation_id === activeId}
                  isRenaming={renamingId === conv.conversation_id}
                  renameValue={renameValue}
                  renameInputRef={renameInputRef}
                  onRenameChange={setRenameValue}
                  onRenameCommit={commitRename}
                  onRenameBlur={commitRename}
                  onSelect={() => onSelect(conv.conversation_id)}
                  onContextMenu={(e) => openMenu(e, conv.conversation_id)}
                  filterSummaryText={conversationScopeParts(conv, t).filter(Boolean).join(" ・ ")} // i18n-ignore: locale-neutral separator
                />
              ))}
            </section>
          )
        )}
      </div>
    </div>
  );

  // ── context menu (shared by both variants) ───────────────────────────────
  const contextMenu = menu && activeConv && (
    <div
      ref={menuRef}
      style={{
        position: "fixed",
        top: menu.y,
        left: menu.x,
        zIndex: 500,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius)",
        boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
        minWidth: 160,
        padding: "var(--space-1) 0",
      }}
    >
      <ContextMenuItem label={t("ask.sidebar.rename")} onClick={() => handleRename(activeConv)} />
      <ContextMenuItem
        label={activeConv.pinned ? t("ask.sidebar.unpin") : t("ask.sidebar.pin")}
        onClick={() => handleTogglePin(activeConv)}
      />
      <div style={{ height: 1, background: "var(--border-subtle)", margin: "var(--space-1) 0" }} />
      <ContextMenuItem
        label={t("ask.sidebar.delete")}
        onClick={() => handleDelete(activeConv)}
        danger
      />
    </div>
  );

  return <>{sidebarContent}{contextMenu}</>;
}

// ─── sub-components ───────────────────────────────────────────────────────────

const groupHeaderStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "var(--text-tertiary)",
  padding: "var(--space-3) var(--space-4) var(--space-1)",
  userSelect: "none",
};

type ConvItemProps = {
  conv: Conversation;
  isActive: boolean;
  isRenaming: boolean;
  renameValue: string;
  renameInputRef: RefObject<HTMLInputElement | null>;
  onRenameChange: (v: string) => void;
  onRenameCommit: (id: string) => void;
  onRenameBlur: (id: string) => void;
  onSelect: () => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onContextMenu: (e: any) => void;
  filterSummaryText: string;
};

function ConvItem({
  conv,
  isActive,
  isRenaming,
  renameValue,
  renameInputRef,
  onRenameChange,
  onRenameCommit,
  onRenameBlur,
  onSelect,
  onContextMenu,
  filterSummaryText,
}: ConvItemProps) {
  const subLine = [relativeTime(conv.updated_at), filterSummaryText].filter(Boolean).join(" ・ "); // i18n-ignore: locale-neutral separator

  return (
    <div
      onContextMenu={onContextMenu}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-2)",
        padding: "8px var(--space-3)",
        background: isActive ? "var(--accent-soft)" : "transparent",
        borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
        transition: "background var(--transition)",
        position: "relative",
        userSelect: "none",
      }}
      onMouseEnter={(e) => {
        if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "var(--bg-soft)";
      }}
      onMouseLeave={(e) => {
        if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "transparent";
      }}
    >
      {isRenaming ? (
        <>
          {/* Emoji */}
          <span style={{ fontSize: 16, lineHeight: 1.5, flexShrink: 0 }}>💬</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <input
              ref={renameInputRef}
              value={renameValue}
              onChange={(e) => onRenameChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onRenameCommit(conv.conversation_id);
                if (e.key === "Escape") onRenameBlur(conv.conversation_id);
                e.stopPropagation();
              }}
              onBlur={() => onRenameBlur(conv.conversation_id)}
              style={{
                width: "100%",
                fontSize: 13,
                padding: "2px 6px",
                borderRadius: "var(--radius)",
                border: "1px solid var(--accent)",
                background: "var(--bg-surface)",
              }}
            />
          </div>
        </>
      ) : (
        // A real <button> (not a div carrying role="button") so it can't
        // validly nest the kebab, which is a sibling instead -- it also
        // gets native keyboard activation and the shared :focus-visible
        // outline for free.
        <button
          type="button"
          onClick={onSelect}
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--space-2)",
            background: "none",
            border: "none",
            padding: 0,
            margin: 0,
            font: "inherit",
            color: "inherit",
            textAlign: "left",
            cursor: "pointer",
          }}
        >
          <span style={{ fontSize: 16, lineHeight: 1.5, flexShrink: 0 }}>💬</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: isActive ? 600 : 400,
                color: "var(--text-primary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                lineHeight: 1.4,
              }}
            >
              {conv.title}
            </div>

            {subLine && (
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  marginTop: 2,
                  lineHeight: 1.3,
                }}
              >
                {subLine}
              </div>
            )}
          </div>
        </button>
      )}

      {/* Kebab / more button */}
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onContextMenu(e); }}
        style={{
          flexShrink: 0,
          background: "none",
          border: "none",
          color: "var(--text-tertiary)",
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          padding: "0 2px",
          opacity: 0.6,
          marginTop: 1,
        }}
        aria-label="More options"
      >
        ⋯
      </button>
    </div>
  );
}

type ContextMenuItemProps = {
  label: string;
  onClick: () => void;
  danger?: boolean;
};

function ContextMenuItem({ label, onClick, danger }: ContextMenuItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        background: "none",
        border: "none",
        padding: "7px var(--space-4)",
        fontSize: 13,
        cursor: "pointer",
        color: danger ? "var(--color-danger)" : "var(--text-primary)",
        transition: "background var(--transition)",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "none";
      }}
    >
      {label}
    </button>
  );
}
