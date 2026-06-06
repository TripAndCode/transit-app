import { useState, useRef, useEffect, useCallback, type CSSProperties, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useConversations, useUpdateConversation, useDeleteConversation } from "../api/hooks";
import type { Conversation, FilterCtx } from "../api/types";
import { relativeTime } from "../utils/relativeTime";

// ─── helpers ─────────────────────────────────────────────────────────────────

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

function isYesterday(iso: string): boolean {
  const d = new Date(iso);
  const yest = new Date();
  yest.setDate(yest.getDate() - 1);
  return (
    d.getFullYear() === yest.getFullYear() &&
    d.getMonth() === yest.getMonth() &&
    d.getDate() === yest.getDate()
  );
}

function isThisWeek(iso: string): boolean {
  const d = new Date(iso).getTime();
  const now = Date.now();
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  return now - d < weekMs && d <= now;
}

function filterSummary(fc: FilterCtx, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const parts: string[] = [];

  // Date range
  if (fc.from_date && fc.to_date) {
    // Try to humanise common ranges
    const from = new Date(fc.from_date);
    const to = new Date(fc.to_date);
    const days = Math.round((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000));
    if (days === 6 || days === 7) parts.push(t("filters.range.last_7d"));
    else if (days >= 28 && days <= 31) parts.push(t("filters.range.last_30d"));
    else if (days >= 85 && days <= 92) parts.push(t("filters.range.last_90d"));
    else parts.push(`${fc.from_date} 〜 ${fc.to_date}`);
  }

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

  return parts.join(" ・ ");
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

  const [mobileOpen, setMobileOpen] = useState(false);
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

  // Focus rename input when opened
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const openMenu = useCallback((e: any, convId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ convId, x: rect.right, y: rect.top });
  }, []);

  const handleRename = useCallback((conv: Conversation) => {
    setMenu(null);
    setRenamingId(conv.conversation_id);
    setRenameValue(conv.title);
  }, []);

  const commitRename = useCallback((convId: string) => {
    const trimmed = renameValue.trim();
    if (trimmed) {
      updateConv.mutate({ id: convId, patch: { title: trimmed } });
    }
    setRenamingId(null);
    setRenameValue("");
  }, [renameValue, updateConv]);

  const handleTogglePin = useCallback((conv: Conversation) => {
    setMenu(null);
    updateConv.mutate({ id: conv.conversation_id, patch: { pinned: !conv.pinned } });
  }, [updateConv]);

  const handleDelete = useCallback((conv: Conversation) => {
    setMenu(null);
    if (window.confirm(t("ask.sidebar.delete_confirm"))) {
      deleteConv.mutate(conv.conversation_id);
      if (activeId === conv.conversation_id) onSelect(null);
    }
  }, [t, deleteConv, activeId, onSelect]);

  // Group conversations
  const pinned = conversations.filter((c) => c.pinned);
  const unpinned = conversations.filter((c) => !c.pinned);

  const todayList = unpinned.filter((c) => isToday(c.updated_at));
  const yesterdayList = unpinned.filter((c) => isYesterday(c.updated_at));
  const thisWeekList = unpinned.filter(
    (c) => !isToday(c.updated_at) && !isYesterday(c.updated_at) && isThisWeek(c.updated_at)
  );
  const earlierList = unpinned.filter(
    (c) => !isToday(c.updated_at) && !isYesterday(c.updated_at) && !isThisWeek(c.updated_at)
  );

  const groups: { labelKey: string; items: Conversation[] }[] = [
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
          onClick={() => { onNewThread(); setMobileOpen(false); }}
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
      <div style={{ flex: 1, overflowY: "auto", padding: "0 0 var(--space-3) 0" }}>
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

        {/* Pinned */}
        {pinned.length > 0 && (
          <section>
            <div style={groupHeaderStyle}>📌 {t("ask.sidebar.pinned")}</div>
            {pinned.map((conv) => (
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
                onSelect={() => { onSelect(conv.conversation_id); setMobileOpen(false); }}
                onContextMenu={(e) => openMenu(e, conv.conversation_id)}
                filterSummaryText={filterSummary(conv.filter_ctx, t)}
              />
            ))}
          </section>
        )}

        {/* Date-grouped */}
        {groups.map(({ labelKey, items }) =>
          items.length === 0 ? null : (
            <section key={labelKey}>
              <div style={groupHeaderStyle}>{t(labelKey)}</div>
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
                  onSelect={() => { onSelect(conv.conversation_id); setMobileOpen(false); }}
                  onContextMenu={(e) => openMenu(e, conv.conversation_id)}
                  filterSummaryText={filterSummary(conv.filter_ctx, t)}
                />
              ))}
            </section>
          )
        )}
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside
        style={{
          width: 240,
          flexShrink: 0,
          background: "var(--bg-surface)",
          borderRight: "1px solid var(--border-soft)",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          position: "relative",
        }}
        className="thread-sidebar-desktop"
      >
        {sidebarContent}
      </aside>

      {/* Mobile: hamburger + slide-in drawer */}
      <div className="thread-sidebar-mobile">
        {/* Hamburger button */}
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label={t("ask.sidebar.new_thread")}
          style={{
            position: "fixed",
            top: 60,
            left: 12,
            zIndex: 200,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            width: 36,
            height: 36,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            boxShadow: "0 1px 4px rgba(0,0,0,0.1)",
          }}
        >
          <span style={{ fontSize: 18, lineHeight: 1 }}>☰</span>
        </button>

        {/* Backdrop */}
        {mobileOpen && (
          <div
            onClick={() => setMobileOpen(false)}
            role="presentation"
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.3)",
              zIndex: 300,
            }}
          />
        )}

        {/* Drawer */}
        <aside
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            bottom: 0,
            width: 260,
            zIndex: 301,
            transform: mobileOpen ? "translateX(0)" : "translateX(-100%)",
            transition: "transform 200ms ease-out",
            background: "var(--bg-surface)",
            borderRight: "1px solid var(--border-soft)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Close row */}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              padding: "var(--space-2) var(--space-3)",
              borderBottom: "1px solid var(--border-soft)",
            }}
          >
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              style={{
                background: "none",
                border: "none",
                fontSize: 20,
                cursor: "pointer",
                color: "var(--text-secondary)",
                lineHeight: 1,
                padding: 4,
              }}
              aria-label={t("common.close")}
            >
              ×
            </button>
          </div>
          <div style={{ flex: 1, overflow: "hidden" }}>{sidebarContent}</div>
        </aside>
      </div>

      {/* Context menu */}
      {menu && activeConv && (
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
      )}

      {/* Responsive CSS */}
      <style>{`
        @media (max-width: 640px) {
          .thread-sidebar-desktop { display: none !important; }
          .thread-sidebar-mobile { display: block; }
        }
        @media (min-width: 641px) {
          .thread-sidebar-desktop { display: flex !important; }
          .thread-sidebar-mobile { display: none; }
        }
      `}</style>
    </>
  );
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
  renameInputRef: RefObject<HTMLInputElement>;
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
  const subLine = [relativeTime(conv.updated_at), filterSummaryText].filter(Boolean).join(" ・ ");

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect(); }}
      onContextMenu={onContextMenu}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-2)",
        padding: "8px var(--space-3)",
        cursor: "pointer",
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
      {/* Emoji */}
      <span style={{ fontSize: 16, lineHeight: 1.5, flexShrink: 0 }}>💬</span>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {isRenaming ? (
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
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              fontSize: 13,
              padding: "2px 6px",
              borderRadius: "var(--radius)",
              border: "1px solid var(--accent)",
              background: "var(--bg-surface)",
            }}
          />
        ) : (
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
        )}

        {!isRenaming && subLine && (
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
        color: danger ? "#c0392b" : "var(--text-primary)",
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
