import { Fragment, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Z_INDEX } from "../../styles/zIndex";
import { useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { td, th } from "../tableStyles";

export type DataTableColumn<Row> = {
  /** Stable column identity; also the React key for the cell. */
  key: string;
  header: ReactNode;
  render: (row: Row) => ReactNode;
  width?: number | string;
  align?: "left" | "right";
};

type SavedView = {
  id: string;
  label: string;
  /** Optional count shown beside the label, e.g. how many rows the view holds. */
  count?: number;
};

/** One line of the shortcut hint: the key as it is typed, and what it does. */
type ShortcutHint = {
  keys: string;
  description: string;
};

type DataTableProps<Row> = {
  /** Accessible name for the table. Required — an admin page usually shows
   *  more than one, and "table" alone tells a screen-reader user nothing. */
  caption: string;
  rows: readonly Row[];
  columns: readonly DataTableColumn<Row>[];
  rowKey: (row: Row) => string;
  /** Human-readable row name for the checkbox's accessible name. */
  rowLabel?: (row: Row) => string;
  /** Renders the leading checkbox column. Keyboard selection (`x`) works
   *  whenever `onSelectionChange` is given, with or without the column. */
  selectable?: boolean;
  selectedIds?: ReadonlySet<string>;
  onSelectionChange?: (next: Set<string>) => void;
  /** Row activation: click or Enter on the focused row. */
  onOpen?: (row: Row) => void;
  savedViews?: readonly SavedView[];
  /** URL search param the saved-view chips read and write. Ignored when
   *  `activeView`/`onSelectView` are supplied. */
  savedViewParam?: string;
  /** Controlled saved views, for a caller whose views are a named
   *  combination of its own existing filter params rather than one opaque
   *  value -- the chips then stay in step with a URL a user can still edit
   *  or bookmark by those params directly. */
  activeView?: string;
  onSelectView?: (id: string) => void;
  /** A newer page is in flight while the previous one stays rendered
   *  (react-query's `keepPreviousData`). Dimmed rather than replaced, so a
   *  reader is told the rows are stale instead of reading them as current. */
  pending?: boolean;
  /** Rows this table must not offer for selection -- e.g. the signed-in
   *  operator's own row on a page whose bulk actions could lock them out.
   *  Their checkbox renders disabled, and they are excluded from the header
   *  checkbox's "all", so select-all does not silently mean "all but one". */
  isRowSelectable?: (row: Row) => boolean;
  /** Seen before the table's own `j`/`k`/`x`/`Enter` handling, for keys that
   *  belong to the page rather than to a table. Call `preventDefault()` to
   *  take the key. */
  onRowKeyDown?: (event: React.KeyboardEvent<HTMLTableRowElement>, row: Row) => void;
  /** Page-level keys the caller handles itself, listed in the toolbar's
   *  shortcut hint after the table's own four. A shortcut a reader cannot
   *  discover is a shortcut nobody uses. */
  extraShortcuts?: readonly ShortcutHint[];
  emptyLabel?: string;
  /** The row a detail surface is currently open on, marked so an operator
   *  scanning the list can still see which one they opened. */
  activeRowKey?: string | null;
};

const EMPTY_SELECTION: ReadonlySet<string> = new Set();

/** Move row focus to the next/previous sibling row, stopping at the ends —
 *  wrapping would silently jump an operator from the last row to the first
 *  mid-scan. Read off the DOM rather than a ref array so the table never has
 *  to hold a parallel list of nodes in sync with its rows. */
function focusSiblingRow(from: HTMLElement, direction: 1 | -1): void {
  const sibling = direction === 1 ? from.nextElementSibling : from.previousElementSibling;
  if (sibling instanceof HTMLElement) sibling.focus();
}

/**
 * The admin section's shared table: sticky header, keyboard row navigation
 * (`j`/`k` move, `x` selects, `Enter` opens) behind a discoverable shortcut
 * hint, an optional checkbox column, and a saved-view chip row backed by the
 * URL so a view is linkable and survives a reload.
 *
 * It is exposed as a `grid` rather than a plain `table`: its rows are
 * interactive and selectable, and `aria-selected` on a row is only
 * meaningful inside a grid that declares `aria-multiselectable`.
 *
 * Exactly one *row* holds the tab stop at a time (roving tabindex), and
 * `j`/`k`/arrow keys move it, instead of every row of a 50-row page being
 * its own tab stop. Controls a caller renders inside a cell -- the selection
 * checkbox, a link, a role picker -- keep their own tab stops, so Tab from
 * the focused row walks that row's controls before leaving the grid. Taking
 * those out of the tab order would need an explicit enter-the-cell key to
 * give them back, which is a bigger contract than this table has today.
 *
 * Selection is lifted to the caller: the pages that use this own bulk
 * actions and undo, and both need the selected set to outlive the table.
 */
export function DataTable<Row>({
  caption,
  rows,
  columns,
  rowKey,
  rowLabel,
  selectable = false,
  selectedIds = EMPTY_SELECTION,
  onSelectionChange,
  onOpen,
  savedViews,
  savedViewParam = "view",
  emptyLabel,
  activeRowKey = null,
  pending = false,
  activeView: controlledView,
  onSelectView,
  isRowSelectable,
  onRowKeyDown: onCallerRowKeyDown,
  extraShortcuts,
}: DataTableProps<Row>) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const activeView = controlledView ?? params.get(savedViewParam) ?? savedViews?.[0]?.id;

  // Which row holds the table's single tab stop. Derived rather than
  // synchronized: when the row set changes under it, a remembered key that
  // is no longer present falls back to the first row on the next render,
  // with no effect needed to write state from.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const keys = rows.map(rowKey);
  const rovingKey = focusedKey !== null && keys.includes(focusedKey) ? focusedKey : keys[0];

  function selectView(id: string) {
    if (onSelectView) {
      onSelectView(id);
      return;
    }
    const next = new URLSearchParams(params);
    next.set(savedViewParam, id);
    setParams(next, { replace: true });
  }

  function selectableRows(): readonly Row[] {
    return isRowSelectable ? rows.filter(isRowSelectable) : rows;
  }

  function toggle(id: string) {
    if (!onSelectionChange) return;
    const next = new Set(selectedIds);
    if (!next.delete(id)) next.add(id);
    onSelectionChange(next);
  }

  function toggleAll() {
    if (!onSelectionChange) return;
    const rowsToSelect = selectableRows();
    const allSelected = rowsToSelect.length > 0 && rowsToSelect.every((row) => selectedIds.has(rowKey(row)));
    onSelectionChange(allSelected ? new Set() : new Set(rowsToSelect.map(rowKey)));
  }

  function onRowKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>, row: Row) {
    onCallerRowKeyDown?.(event, row);
    if (event.defaultPrevented) return;
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      focusSiblingRow(event.currentTarget, 1);
    } else if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      focusSiblingRow(event.currentTarget, -1);
    } else if (event.key === "x") {
      if (isRowSelectable && !isRowSelectable(row)) return;
      event.preventDefault();
      toggle(rowKey(row));
    } else if (event.key === "Enter") {
      event.preventDefault();
      onOpen?.(row);
    }
  }

  const selectableList = selectableRows();
  const allSelected = selectableList.length > 0 && selectableList.every((row) => selectedIds.has(rowKey(row)));

  const shortcuts: ShortcutHint[] = [
    { keys: "j", description: t("admin.table.shortcuts.next") },
    { keys: "k", description: t("admin.table.shortcuts.prev") },
    { keys: "x", description: t("admin.table.shortcuts.select") },
    { keys: "Enter", description: t("admin.table.shortcuts.open") },
    ...(extraShortcuts ?? []),
  ];
  const hasSavedViews = savedViews != null && savedViews.length > 0;

  return (
    <div style={{ opacity: pending ? 0.6 : 1, transition: "opacity var(--transition)" }}>
      {(hasSavedViews || rows.length > 0) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            flexWrap: "wrap",
            marginBottom: 10,
          }}
        >
          <div
            role="group"
            aria-label={t("admin.table.saved_views")}
            style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
          >
            {savedViews?.map((view) => {
              const on = view.id === activeView;
              return (
                <button
                  key={view.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => selectView(view.id)}
                  style={{
                    fontSize: 12,
                    padding: "3px 10px",
                    borderRadius: 999,
                    cursor: "pointer",
                    fontFamily: "inherit",
                    border: `1px solid ${on ? "var(--accent)" : "var(--border-subtle)"}`,
                    background: on ? "var(--accent-soft)" : "transparent",
                    color: on ? "var(--accent)" : "var(--text-secondary)",
                  }}
                >
                  {view.label}
                  {view.count != null && <span style={{ marginLeft: 6, opacity: 0.8 }}>{view.count}</span>}
                </button>
              );
            })}
          </div>
          {rows.length > 0 && <ShortcutHintChip shortcuts={shortcuts} />}
        </div>
      )}

      {rows.length === 0 ? (
        <p style={{ color: "var(--text-secondary)", fontSize: 14, padding: "14px 2px" }}>
          {emptyLabel ?? t("admin.table.empty")}
        </p>
      ) : (
        <div style={{ overflow: "auto", maxHeight: "70vh" }}>
          <table
            role="grid"
            aria-multiselectable={selectable ? true : undefined}
            style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0, fontSize: 13 }}
          >
            <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
              {caption}
            </caption>
            <thead>
              <tr role="row">
                {selectable && (
                  <th role="columnheader" style={th({ width: 34, sticky: true })}>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label={t("admin.table.select_all")}
                    />
                  </th>
                )}
                {columns.map((column) => (
                  <th
                    key={column.key}
                    role="columnheader"
                    style={th({ width: column.width, align: column.align ?? "left", sticky: true })}
                  >
                    {column.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const id = rowKey(row);
                const selected = selectedIds.has(id);
                const active = id === activeRowKey;
                return (
                  <tr
                    key={id}
                    role="row"
                    tabIndex={id === rovingKey ? 0 : -1}
                    aria-selected={selectable ? selected : undefined}
                    aria-current={active ? "true" : undefined}
                    onFocus={() => setFocusedKey(id)}
                    onKeyDown={(event) => onRowKeyDown(event, row)}
                    onClick={() => onOpen?.(row)}
                    style={{
                      cursor: onOpen ? "pointer" : "default",
                      // Selection wins the background: a bulk action needs to
                      // show its whole set, and only one row is ever active.
                      background: selected
                        ? "var(--accent-soft)"
                        : active
                          ? "var(--hover-tint)"
                          : "transparent",
                    }}
                  >
                    {selectable && (
                      <td role="gridcell" style={td()}>
                        <input
                          type="checkbox"
                          checked={selected}
                          // Disabled rather than absent: an empty cell reads
                          // as a rendering gap, where a disabled box says the
                          // row is deliberately out of reach.
                          disabled={!(isRowSelectable?.(row) ?? true)}
                          onClick={(event) => event.stopPropagation()}
                          onChange={() => toggle(id)}
                          aria-label={t("admin.table.select_row", { label: rowLabel?.(row) ?? id })}
                        />
                      </td>
                    )}
                    {columns.map((column) => (
                      <td key={column.key} role="gridcell" style={td({ align: column.align ?? "left" })}>
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Toolbar chip disclosing the table's keyboard shortcuts. The keys are
 *  otherwise invisible: without this, the only way to learn them is to read
 *  the source. */
function ShortcutHintChip({ shortcuts }: { shortcuts: readonly ShortcutHint[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          fontSize: 12,
          padding: "3px 10px",
          borderRadius: 999,
          cursor: "pointer",
          fontFamily: "inherit",
          border: "1px solid var(--border-subtle)",
          background: "transparent",
          color: "var(--text-secondary)",
        }}
      >
        <span aria-hidden="true">⌨</span>
        {t("admin.table.shortcuts.trigger")}
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={t("admin.table.shortcuts.title")}
          style={{
            position: "absolute",
            top: "100%",
            right: 0,
            marginTop: 6,
            zIndex: Z_INDEX.dropdown,
            minWidth: 200,
            padding: "10px 12px",
            background: "var(--surface-1)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--el-2)",
          }}
        >
          <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 10px", fontSize: 12 }}>
            {shortcuts.map((shortcut) => (
              <Fragment key={shortcut.keys}>
                <dt style={{ margin: 0 }}>
                  <kbd
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--text-xs)",
                      padding: "1px 6px",
                      borderRadius: 4,
                      border: "1px solid var(--border-subtle)",
                      background: "var(--surface-2)",
                      color: "var(--text-primary)",
                    }}
                  >
                    {shortcut.keys}
                  </kbd>
                </dt>
                <dd style={{ margin: 0, color: "var(--text-secondary)" }}>{shortcut.description}</dd>
              </Fragment>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
