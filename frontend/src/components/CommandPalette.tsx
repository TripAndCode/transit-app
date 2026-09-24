import {
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { useLocation, useMatch, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Search } from "lucide-react";
import { useAgencies, useRoutes } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import { ctxToQueryString, useRangeContext, type TimeBand } from "../api/rangeContext";
import { buildTimeBandOptions } from "./timeBandOptions";
import { REPORT_TYPE_IDS, buildReportTypeLabels } from "../tabs/reportTypes";
import { useTheme } from "../styles/useTheme";
import { filterItems, type Searchable } from "./commandPaletteMatch";
import { onActivateKey } from "../utils/a11y";
import { COMMAND_PALETTE_OPEN_EVENT } from "./commandPaletteEvents";
import "./commandPalette.css";

const RECENTS_KEY = "transit.commandPaletteRecents";
const MAX_RECENTS = 8;
const GO_CHORD_TIMEOUT_MS = 900;

/** The four keyboard-reachable destinations, doubling as the palette's
 *  "移動" group and the `g` + letter chords. A separate table from
 *  Sidebar.tsx's SIDEBAR_NAV_ITEMS rather than reusing it directly: three of
 *  the four (all but "ask", which the sidebar renders as a distinct CTA, not
 *  a nav item) would still need a second, palette-only table for the chord
 *  key and search sublabel, so a from-scratch table of all four together is
 *  the simpler single source for this list specifically. */
const GO_TO_TARGETS = [
  { to: "operations", chordKey: "o", labelKey: "design:overview", sublabelKey: "design:live" },
  { to: "route-analysis", chordKey: "a", labelKey: "design:analysis", sublabelKey: "design:investigate" },
  { to: "reports", chordKey: "r", labelKey: "design:reports", sublabelKey: "design:summary" },
  { to: "ask", chordKey: "q", labelKey: "nav.ask", sublabelKey: "palette.nav_ask_sublabel" },
] as const;

type PaletteGroup = "recent" | "nav" | "agency" | "route" | "report" | "timeband" | "action";

type PaletteItem = Searchable & {
  id: string;
  group: PaletteGroup;
  keys?: string[];
  run: () => void;
};

function readRecentIds(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeRecentIds(ids: string[]): void {
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(ids.slice(0, MAX_RECENTS)));
  } catch {
    /* localStorage unavailable (private browsing, quota) — recents just don't persist */
  }
}

function pushRecentId(id: string): string[] {
  const next = [id, ...readRecentIds().filter((existing) => existing !== id)].slice(0, MAX_RECENTS);
  writeRecentIds(next);
  return next;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Escape-to-close, a Tab-cycling focus trap, and restoring focus to
 * whatever was focused before opening — the three behaviors a native
 * `<dialog>` gives for free and a plain overlay `<div>` does not. Shared by
 * the palette and the shortcut sheet rather than duplicated, since neither
 * dialog in this app is available from another (still-unmerged) branch.
 */
function Dialog({
  onClose,
  ariaLabel,
  className,
  initialFocusRef,
  children,
}: {
  onClose: () => void;
  ariaLabel: string;
  className: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const toFocus = initialFocusRef?.current ?? containerRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ?? null;
    toFocus?.focus();

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const container = containerRef.current;
      if (!container) return;
      const focusables = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !container.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !container.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onClose/initialFocusRef intentionally read once per mount; this effect owns one dialog's lifetime, not a value that should reopen it
  }, []);

  return (
    <div
      className="cmdp-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={containerRef} className={className} role="dialog" aria-modal="true" aria-label={ariaLabel}>
        {children}
      </div>
    </div>
  );
}

function buildAgencyItems(
  agencies: { agency_id: number; agency_name: string }[] | undefined,
  goToAgency: (id: number) => void,
): PaletteItem[] {
  if (!agencies) return [];
  return agencies.map((a) => ({
    id: `agency:${a.agency_id}`,
    group: "agency",
    label: a.agency_name,
    run: () => goToAgency(a.agency_id),
  }));
}

function buildRouteItems(
  routes: { route_code: string | null }[] | undefined,
  formatRoute: (code: string | null | undefined) => string,
  goToRoute: (code: string) => void,
): PaletteItem[] {
  if (!routes) return [];
  const items: PaletteItem[] = [];
  for (const r of routes) {
    if (!r.route_code) continue;
    items.push({
      id: `route:${r.route_code}`,
      group: "route",
      label: formatRoute(r.route_code),
      sublabel: r.route_code,
      run: () => goToRoute(r.route_code!),
    });
  }
  return items;
}

function buildReportItems(t: TFunction, agencyId: number | null, goToReport: (id: string) => void): PaletteItem[] {
  if (agencyId == null) return [];
  const labels = buildReportTypeLabels(t);
  return REPORT_TYPE_IDS.map((id) => ({
    id: `report:${id}`,
    group: "report",
    label: labels[id],
    sublabel: `analysis/${id}`,
    run: () => goToReport(id),
  }));
}

function buildTimeBandItems(t: TFunction, agencyId: number | null, goToTimeBand: (band: TimeBand) => void): PaletteItem[] {
  if (agencyId == null) return [];
  return buildTimeBandOptions(t)
    .filter((opt) => opt.value !== "all")
    .map((opt) => ({
      id: `timeband:${opt.value}`,
      group: "timeband",
      label: opt.label,
      run: () => goToTimeBand(opt.value),
    }));
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const agencyParam = useMatch("/agencies/:agencyId/*")?.params.agencyId;
  const tabParam = useMatch("/agencies/:agencyId/:tab/*")?.params.tab;
  const agencyId = agencyParam ? Number(agencyParam) : null;
  const [ctx] = useRangeContext();
  const [theme, setTheme] = useTheme();

  const { data: agencies } = useAgencies();
  const { data: routes } = useRoutes(agencyId);
  const { format: formatRoute } = useRouteNames(agencyId);

  const [open, setOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [recentIds, setRecentIds] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const ctxQueryString = ctxToQueryString(ctx);
  const ctxSuffix = ctxQueryString ? `?${ctxQueryString}` : "";

  function openPalette() {
    setRecentIds(readRecentIds());
    setQuery("");
    setActiveIndex(0);
    setOpen(true);
  }

  function closePalette() {
    setOpen(false);
  }

  function openSheet() {
    setOpen(false);
    setSheetOpen(true);
  }

  function goToNav(to: string) {
    if (agencyId == null) return;
    navigate(`/agencies/${agencyId}/${to}${ctxSuffix}`);
  }

  function goToAgency(id: number) {
    navigate(`/agencies/${id}/${tabParam ?? "overview"}`);
  }

  function goToRoute(code: string) {
    if (agencyId == null) return;
    const qs = ctxToQueryString({ ...ctx, routes: [code] });
    navigate(`/agencies/${agencyId}/route-analysis${qs ? `?${qs}` : ""}`);
  }

  function goToReport(reportType: string) {
    if (agencyId == null) return;
    navigate(`/agencies/${agencyId}/analysis/${reportType}${ctxSuffix}`);
  }

  function goToTimeBand(band: TimeBand) {
    const qs = ctxToQueryString({ ...ctx, time_band: band });
    navigate(`${location.pathname}${qs ? `?${qs}` : ""}`);
  }

  function cycleTheme() {
    setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system");
  }

  const navItems: PaletteItem[] = GO_TO_TARGETS.map((target) => ({
    id: `nav:${target.to}`,
    group: "nav",
    label: t(target.labelKey),
    sublabel: t(target.sublabelKey),
    keys: ["g", target.chordKey],
    run: () => goToNav(target.to),
  }));

  const agencyItems = buildAgencyItems(agencies, goToAgency);
  const routeItems = buildRouteItems(routes, formatRoute, goToRoute);
  const reportItems = buildReportItems(t, agencyId, goToReport);
  const timeBandItems = buildTimeBandItems(t, agencyId, goToTimeBand);
  const actionItems: PaletteItem[] = [
    {
      id: "action:theme",
      group: "action",
      label: t("palette.action.toggle_theme"),
      sublabel: t("palette.action.toggle_theme_sublabel"),
      run: cycleTheme,
    },
    {
      id: "action:shortcuts",
      group: "action",
      label: t("palette.action.shortcuts"),
      keys: ["?"],
      run: openSheet,
    },
  ];

  const browsableItems = [
    ...(agencyId != null ? navItems : []),
    ...agencyItems,
    ...routeItems,
    ...reportItems,
    ...timeBandItems,
    ...actionItems,
  ];
  const itemsById = new Map(browsableItems.map((item) => [item.id, item]));

  const trimmedQuery = query.trim();
  const recentItems: PaletteItem[] = trimmedQuery
    ? []
    : recentIds
        .map((id) => itemsById.get(id))
        .filter((item): item is PaletteItem => item != null)
        // Shown under its own "Recent" header, not the item's normal group —
        // the underlying item (and its `run`) is otherwise unchanged.
        .map((item) => ({ ...item, group: "recent" as const }));
  // Recents are also shown in their own group below, in browse mode —
  // exclude them from the main list there so each item renders (and keys)
  // exactly once. A search query bypasses this: a matched item should show
  // once, in its normal group, not disappear because it was recently used.
  const recentIdSet = new Set(recentItems.map((item) => item.id));
  const filtered = trimmedQuery ? filterItems(browsableItems, query) : browsableItems.filter((item) => !recentIdSet.has(item.id));
  const visibleItems = [...recentItems, ...filtered];

  function runItem(item: PaletteItem) {
    setRecentIds(pushRecentId(item.id));
    closePalette();
    item.run();
  }

  const pendingGoRef = useRef(false);
  const pendingGoTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  function clearPendingGo() {
    pendingGoRef.current = false;
    if (pendingGoTimerRef.current) clearTimeout(pendingGoTimerRef.current);
  }

  const handleGlobalKeyDown = useEffectEvent((e: KeyboardEvent) => {
    if (open || sheetOpen) return; // the open dialog owns Escape/Tab/arrows itself
    if (isTypingTarget(e.target)) return;

    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) {
      clearPendingGo();
      return;
    }
    if (pendingGoRef.current) {
      clearPendingGo();
      const target = GO_TO_TARGETS.find((g) => g.chordKey === e.key.toLowerCase());
      if (target) goToNav(target.to);
      return;
    }
    if (e.key.toLowerCase() === "g") {
      pendingGoRef.current = true;
      pendingGoTimerRef.current = setTimeout(clearPendingGo, GO_CHORD_TIMEOUT_MS);
      return;
    }
    if (e.key === "?") {
      setOpen(false);
      setSheetOpen(true);
    }
  });

  useEffect(() => {
    document.addEventListener("keydown", handleGlobalKeyDown);
    return () => {
      document.removeEventListener("keydown", handleGlobalKeyDown);
      clearPendingGo();
    };
  }, []);

  useEffect(() => {
    function onOpenEvent() {
      openPalette();
    }
    window.addEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenEvent);
    return () => window.removeEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenEvent);
  }, []);

  function onInputKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(visibleItems.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = visibleItems[activeIndex];
      if (item) runItem(item);
    }
  }

  let lastGroup: PaletteGroup | null = null;

  return (
    <>
      {open && (
        <Dialog onClose={closePalette} ariaLabel={t("palette.aria_label")} className="cmdp-palette" initialFocusRef={inputRef}>
          <div className="cmdp-input-row">
            <Search size={16} strokeWidth={1.75} aria-hidden="true" className="cmdp-input-icon" />
            <input
              ref={inputRef}
              type="text"
              className="cmdp-input"
              value={query}
              placeholder={t("palette.placeholder")}
              autoComplete="off"
              role="combobox"
              aria-expanded="true"
              aria-controls="cmdp-listbox"
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={onInputKeyDown}
            />
          </div>
          <ul id="cmdp-listbox" className="cmdp-list" role="listbox">
            {visibleItems.length === 0 && <li className="cmdp-empty">{t("palette.no_results")}</li>}
            {visibleItems.map((item, index) => {
              const showHeader = lastGroup !== item.group;
              lastGroup = item.group;
              return (
                <li key={item.id}>
                  {showHeader && <div className="cmdp-group-label">{t(`palette.group.${item.group}`)}</div>}
                  <div
                    role="option"
                    aria-selected={index === activeIndex}
                    className="cmdp-item"
                    // Not in the Tab sequence (tabIndex={-1} + the Dialog's
                    // focus trap excludes it): the input owns keyboard focus
                    // and arrow-key selection, matching the ARIA combobox
                    // pattern. This is still a real activation target for a
                    // screen reader user who navigates onto it directly.
                    tabIndex={-1}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => runItem(item)}
                    onKeyDown={onActivateKey(() => runItem(item))}
                  >
                    <span className="cmdp-item-text">
                      <span className="cmdp-item-label">{item.label}</span>
                      {item.sublabel && <span className="cmdp-item-sublabel">{item.sublabel}</span>}
                    </span>
                    {item.keys && (
                      <span className="cmdp-item-keys">
                        {item.keys.map((k, i) => (
                          <kbd key={i} className="cmdp-kbd">
                            {k}
                          </kbd>
                        ))}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="cmdp-footer">
            <span>
              <kbd className="cmdp-kbd">↑↓</kbd> {t("palette.footer.navigate")}
            </span>
            <span>
              <kbd className="cmdp-kbd">↵</kbd> {t("palette.footer.select")}
            </span>
            <span>
              <kbd className="cmdp-kbd">esc</kbd> {t("common.close")}
            </span>
          </div>
        </Dialog>
      )}
      {sheetOpen && (
        <Dialog onClose={() => setSheetOpen(false)} ariaLabel={t("palette.shortcuts.title")} className="cmdp-sheet">
          <div className="cmdp-sheet-header">
            <h2 className="cmdp-sheet-title">{t("palette.shortcuts.title")}</h2>
            <button type="button" className="cmdp-sheet-close" onClick={() => setSheetOpen(false)} aria-label={t("common.close")}>
              ×
            </button>
          </div>
          <ul className="cmdp-sheet-list">
            <li>
              <span>{t("palette.shortcuts.open_palette")}</span>
              <span className="cmdp-item-keys">
                <kbd className="cmdp-kbd">⌘</kbd>
                <kbd className="cmdp-kbd">K</kbd>
              </span>
            </li>
            {GO_TO_TARGETS.map((target) => (
              <li key={target.to}>
                <span>{t("palette.shortcuts.go_to", { target: t(target.labelKey) })}</span>
                <span className="cmdp-item-keys">
                  <kbd className="cmdp-kbd">g</kbd>
                  <kbd className="cmdp-kbd">{target.chordKey}</kbd>
                </span>
              </li>
            ))}
            <li>
              <span>{t("palette.shortcuts.open_shortcuts")}</span>
              <span className="cmdp-item-keys">
                <kbd className="cmdp-kbd">?</kbd>
              </span>
            </li>
            <li>
              <span>{t("palette.shortcuts.extend_brush")}</span>
              <span className="cmdp-item-keys">
                <kbd className="cmdp-kbd">⇧</kbd>
                <kbd className="cmdp-kbd">←/→</kbd>
              </span>
            </li>
          </ul>
        </Dialog>
      )}
    </>
  );
}
