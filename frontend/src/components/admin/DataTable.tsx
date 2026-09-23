import { useTranslation } from "react-i18next";
import { Z_INDEX } from "../../styles/zIndex";
import { useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";

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
  /** Rows this table must not offer for selection -- e.g. the signed-in
   *  operator's own row on a page whose bulk actions could lock them out.
   *  Their checkbox renders disabled, and they are excluded from the header
   *  checkbox's "all", so select-all does not silently mean "all but one". */
  isRowSelectable?: (row: Row) => boolean;
  /** Seen before the table's own `j`/`k`/`x`/`Enter` handling, for keys that
   *  belong to the page rather than to a table. Call `preventDefault()` to
   *  take the key. */
  onRowKeyDown?: (event: React.KeyboardEvent<HTMLTableRowElement>, row: Row) => void;
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
 * (`j`/`k` move, `x` selects, `Enter` opens), an optional checkbox column,
 * and a saved-view chip row backed by the URL so a view is linkable and
 * survives a reload.
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
  activeView: controlledView,
  onSelectView,
  isRowSelectable,
  onRowKeyDown: onCallerRowKeyDown,
}: DataTableProps<Row>) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const activeView = controlledView ?? params.get(savedViewParam) ?? savedViews?.[0]?.id;

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
    if (event.key === "j") {
      event.preventDefault();
      focusSiblingRow(event.currentTarget, 1);
    } else if (event.key === "k") {
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

  return (
    <div>
      {savedViews && savedViews.length > 0 && (
        <div
          role="group"
          aria-label={t("admin.table.saved_views")}
          style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}
        >
          {savedViews.map((view) => {
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
      )}

      {rows.length === 0 ? (
        <p style={{ color: "var(--text-secondary)", fontSize: 14, padding: "14px 2px" }}>
          {emptyLabel ?? t("admin.table.empty")}
        </p>
      ) : (
        <div style={{ overflow: "auto", maxHeight: "70vh" }}>
          <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0, fontSize: 13 }}>
            <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
              {caption}
            </caption>
            <thead>
              <tr>
                {selectable && (
                  <th style={{ ...HEADER_STYLE, width: 34 }}>
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
                    style={{ ...HEADER_STYLE, width: column.width, textAlign: column.align ?? "left" }}
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
                    tabIndex={0}
                    aria-selected={selectable ? selected : undefined}
                    aria-current={active ? "true" : undefined}
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
                      <td style={CELL_STYLE}>
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
                      <td key={column.key} style={{ ...CELL_STYLE, textAlign: column.align ?? "left" }}>
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

const HEADER_STYLE: React.CSSProperties = {
  position: "sticky",
  top: 0,
  zIndex: Z_INDEX.raised,
  background: "var(--surface-1)",
  textAlign: "left",
  padding: "8px 10px",
  fontSize: "var(--text-xs)",
  fontWeight: 600,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "var(--text-secondary)",
  borderBottom: "1px solid var(--border-subtle)",
};

const CELL_STYLE: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: "1px solid var(--surface-2)",
  verticalAlign: "middle",
};
