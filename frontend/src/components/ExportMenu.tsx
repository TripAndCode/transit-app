import { useEffect, useRef, useState, type FocusEvent as ReactFocusEvent, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { Camera, Download, Link2, Printer, Image as ImageIcon } from "lucide-react";
import type { Scope } from "../api/scope";
import { createSnapshot, type SnapshotSpec } from "../api/snapshots";
import { useIsAdmin } from "../api/useIsAdmin";
import { buildCsv, downloadCsv, triggerBlobDownload, type CsvColumn } from "./analysis/csv";
import { svgToPngBlob } from "./exportPng";
import { menuItems, nextMenuItem } from "./menuKeys";
import { usePopoverDismiss } from "../hooks/usePopoverDismiss";

type CsvExportSpec<T> = {
  filenameBase: string;
  rows: T[];
  columns: CsvColumn<T>[];
  ctx?: Scope | null;
  /** Extra rows appended after the `buildCsv` block -- for a tab whose "one"
   *  export genuinely combines more than one table (e.g. the reports tab's
   *  trend + ranking sections). Build each with `buildCsv` too. */
  extraRows?: unknown[][];
};

type ExportMenuProps<T> = {
  /** Element wrapping the tab's primary chart. Its first descendant `<svg>`
   *  is rasterized for the PNG export. Omitted -> no PNG item. */
  svgContainerRef?: RefObject<HTMLElement | null>;
  pngFilenameBase?: string;
  /** Omitted or `null` -> no CSV item. */
  csv?: CsvExportSpec<T> | null;
  /** Default `true`. */
  showPrint?: boolean;
  /** The screen a frozen snapshot would store, with the query its own report
   *  endpoint was asked. Omitted or `null` -> no snapshot item, which admins
   *  alone see even when given. */
  snapshot?: SnapshotSpec | null;
};

type Status = "idle" | "copied" | "fallback" | "pngFailed" | "snapshotPending" | "snapshotCopied" | "snapshotFallback" | "snapshotFailed";

/**
 * One export menu per tab header: PNG (rasterized from the tab's primary
 * SVG), CSV (via the shared `buildCsv` helper), a link to the exact current
 * view (every `useUrlState`/`useScope` key is already in the URL, so
 * this is just the current location — no separate query-string assembly to
 * keep in sync), print, and for an admin a frozen snapshot: a link to the
 * figures the screen shows now, which a later re-analyze does not change.
 */
export function ExportMenu<T>({ svgContainerRef, pngFilenameBase, csv, showPrint = true, snapshot }: ExportMenuProps<T>) {
  const { t } = useTranslation("design");
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  // The link a status line names: the current view's, or a new snapshot's.
  const [statusUrl, setStatusUrl] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const fallbackInputRef = useRef<HTMLInputElement>(null);

  const currentUrl = `${window.location.origin}${location.pathname}${location.search}`;

  usePopoverDismiss(open, rootRef, (reason) => {
    setOpen(false);
    if (reason === "escape") triggerRef.current?.focus();
  });

  // A menu that opens without taking focus strands a keyboard user behind
  // the trigger, tabbing through the rest of the page to reach items that
  // are already on screen in front of them.
  useEffect(() => {
    if (!open) return;
    menuItems(panelRef.current)[0]?.focus();
  }, [open]);

  useEffect(() => {
    if (status === "fallback" || status === "snapshotFallback") fallbackInputRef.current?.select();
  }, [status]);

  function closeMenu() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const target = nextMenuItem(menuItems(panelRef.current), document.activeElement, event.key);
    if (!target) return;
    event.preventDefault();
    target.focus();
  }

  /** Copies `url`, or hands it over in a selectable input where the
   *  clipboard refuses. */
  async function copyLink(url: string, copied: "copied" | "snapshotCopied", fallback: "fallback" | "snapshotFallback") {
    setStatusUrl(url);
    const clipboard = navigator.clipboard;
    if (clipboard?.writeText) {
      try {
        await clipboard.writeText(url);
        setStatus(copied);
        return true;
      } catch {
        // fall through to the selectable-input fallback below -- some
        // browsers (and every non-secure origin) have no working Clipboard API.
      }
    }
    setStatus(fallback);
    return false;
  }

  async function handleCopyLink() {
    if (await copyLink(currentUrl, "copied", "fallback")) closeMenu();
  }

  async function handleSnapshot() {
    if (!snapshot) return;
    closeMenu();
    setStatus("snapshotPending");
    let url: string;
    try {
      url = `${window.location.origin}${(await createSnapshot(snapshot)).path}`;
    } catch {
      setStatus("snapshotFailed");
      return;
    }
    await copyLink(url, "snapshotCopied", "snapshotFallback");
  }

  function handleCsvClick() {
    if (!csv) return;
    downloadCsv(csv.filenameBase, [...buildCsv(csv.rows, csv.columns, csv.ctx), ...(csv.extraRows ?? [])]);
    closeMenu();
  }

  async function handlePng() {
    const svg = svgContainerRef?.current?.querySelector("svg");
    if (!svg) return;
    const filename = `${(pngFilenameBase ?? "export").replace(/[^\w.-]/g, "_")}.png`;
    try {
      const blob = await svgToPngBlob(svg);
      triggerBlobDownload(blob, filename);
      closeMenu();
    } catch {
      setStatus("pngFailed");
    }
  }

  function handlePrint() {
    closeMenu();
    window.print();
  }

  // Tab out of the last item and the menu is gone. Escape and an outside
  // click already close it, but neither fires when focus simply walks off the
  // end -- leaving a mounted `role="menu"` and an `aria-expanded="true"`
  // trigger describing something the user has left behind.
  function handleFocusOut(event: ReactFocusEvent<HTMLDivElement>) {
    if (!open) return;
    const next = event.relatedTarget;
    if (next instanceof Node && rootRef.current?.contains(next)) return;
    setOpen(false);
  }

  return (
    <div className="export-menu" ref={rootRef} onBlur={handleFocusOut}>
      <button
        ref={triggerRef}
        type="button"
        className="btn btn--secondary btn--sm export-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Download size={13} aria-hidden="true" />
        {t("exportMenu")}
      </button>
      {open && (
        <div
          ref={panelRef}
          role="menu"
          // Programmatically focusable only: the menu container itself is
          // never a tab stop, but it owns the arrow-key handling for the
          // items inside it.
          tabIndex={-1}
          className="export-menu__panel"
          aria-label={t("exportMenu")}
          onKeyDown={onMenuKeyDown}
        >
          {svgContainerRef && (
            <button type="button" role="menuitem" onClick={() => void handlePng()}>
              <ImageIcon size={13} aria-hidden="true" />
              {t("downloadPng")}
            </button>
          )}
          {csv && (
            <button type="button" role="menuitem" onClick={handleCsvClick}>
              <Download size={13} aria-hidden="true" />
              {t("csv")}
            </button>
          )}
          <button type="button" role="menuitem" onClick={() => void handleCopyLink()}>
            <Link2 size={13} aria-hidden="true" />
            {t("shareLink")}
          </button>
          {showPrint && (
            <button type="button" role="menuitem" onClick={handlePrint}>
              <Printer size={13} aria-hidden="true" />
              {t("print")}
            </button>
          )}
          {snapshot && <SnapshotItem onSelect={() => void handleSnapshot()} />}
        </div>
      )}
      {status === "copied" && <span role="status" className="export-menu__status">{t("copied")}</span>}
      {status === "pngFailed" && <span role="status" className="export-menu__status">{t("pngFailed")}</span>}
      {status === "snapshotPending" && <span role="status" className="export-menu__status">{t("snapshotPending")}</span>}
      {status === "snapshotFailed" && <span role="status" className="export-menu__status">{t("snapshotFailed")}</span>}
      {status === "snapshotCopied" && (
        <span role="status" className="export-menu__status">
          {t("snapshotCopied")} <a href={statusUrl}>{statusUrl}</a>
        </span>
      )}
      {(status === "fallback" || status === "snapshotFallback") && (
        <span className="export-menu__fallback">
          <label>
            {t(status === "fallback" ? "copyLinkFallback" : "snapshotFallback")}
            <input ref={fallbackInputRef} type="text" readOnly value={statusUrl} onFocus={(e) => e.currentTarget.select()} />
          </label>
        </span>
      )}
    </div>
  );
}

/** Freezing a screen's figures is an admin's act: the item shows only to
 *  one, and asks who the reader is only once the menu is open. */
function SnapshotItem({ onSelect }: { onSelect: () => void }) {
  const { t } = useTranslation("design");
  if (!useIsAdmin()) return null;
  return (
    <button type="button" role="menuitem" onClick={onSelect}>
      <Camera size={13} aria-hidden="true" />
      {t("snapshot")}
    </button>
  );
}
