import { useEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { Download, Link2, Printer, Image as ImageIcon } from "lucide-react";
import type { RangeCtx } from "../api/rangeContext";
import { buildCsv, downloadCsv, triggerBlobDownload, type CsvColumn } from "./analysis/csv";
import { svgToPngBlob } from "./exportPng";

type CsvExportSpec<T> = {
  filenameBase: string;
  rows: T[];
  columns: CsvColumn<T>[];
  ctx?: RangeCtx | null;
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
};

/**
 * One export menu per tab header: PNG (rasterized from the tab's primary
 * SVG), CSV (via the shared `buildCsv` helper), a link to the exact current
 * view (every `useUrlState`/`useRangeContext` key is already in the URL, so
 * this is just the current location — no separate query-string assembly to
 * keep in sync, unlike the old per-tab `copyShareLink` functions), and print.
 */
export function ExportMenu<T>({ svgContainerRef, pngFilenameBase, csv, showPrint = true }: ExportMenuProps<T>) {
  const { t } = useTranslation("design");
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<"idle" | "copied" | "fallback" | "pngFailed">("idle");
  const rootRef = useRef<HTMLDivElement>(null);
  const fallbackInputRef = useRef<HTMLInputElement>(null);

  const currentUrl = `${window.location.origin}${location.pathname}${location.search}`;

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
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

  useEffect(() => {
    if (status === "fallback") fallbackInputRef.current?.select();
  }, [status]);

  function closeMenu() {
    setOpen(false);
  }

  async function handleCopyLink() {
    const clipboard = navigator.clipboard;
    if (clipboard?.writeText) {
      try {
        await clipboard.writeText(currentUrl);
        setStatus("copied");
        closeMenu();
        return;
      } catch {
        // fall through to the selectable-input fallback below -- some
        // browsers (and every non-secure origin) have no working Clipboard API.
      }
    }
    setStatus("fallback");
  }

  function handleCsvClick() {
    if (!csv) return;
    downloadCsv(csv.filenameBase, [...buildCsv(csv.rows, csv.columns, csv.ctx), ...(csv.extraRows ?? [])]);
    closeMenu();
  }

  async function handlePng() {
    const svg = svgContainerRef?.current?.querySelector("svg");
    if (!svg) return;
    try {
      const blob = await svgToPngBlob(svg);
      triggerBlobDownload(blob, `${(pngFilenameBase ?? "export").replace(/[^\w.-]/g, "_")}.png`);
      closeMenu();
    } catch {
      setStatus("pngFailed");
    }
  }

  function handlePrint() {
    closeMenu();
    window.print();
  }

  return (
    <div className="export-menu" ref={rootRef}>
      <button
        type="button"
        className="btn-ghost export-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Download size={13} aria-hidden="true" />
        {t("exportMenu")}
      </button>
      {open && (
        <div role="menu" className="export-menu__panel" aria-label={t("exportMenu")}>
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
        </div>
      )}
      {status === "copied" && <span role="status" className="export-menu__status">{t("copied")}</span>}
      {status === "pngFailed" && <span role="status" className="export-menu__status">{t("pngFailed")}</span>}
      {status === "fallback" && (
        <span className="export-menu__fallback">
          <label>
            {t("copyLinkFallback")}
            <input ref={fallbackInputRef} type="text" readOnly value={currentUrl} onFocus={(e) => e.currentTarget.select()} />
          </label>
        </span>
      )}
    </div>
  );
}
