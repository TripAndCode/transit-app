import { useEffect, useEffectEvent, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useMatch, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ChevronDown } from "lucide-react";
import { useAgencies } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { pushRecentAgency, readRecentAgencies } from "../api/recentAgencies";
import type { Agency } from "../api/types";
import { onActivateKey } from "../utils/a11y";
import { AGENCY_SWITCHER_OPEN_EVENT } from "./agencySwitcherEvents";
import {
  agencyFreshness,
  buildAgencySwitchPath,
  orderAgenciesForSwitcher,
  typeAheadIndex,
  type FreshnessLevel,
} from "./agencySwitcherModel";
import "./agencySwitcher.css";

/** How long a type-ahead buffer keeps accumulating before the next keystroke
 *  starts a fresh search, matching the dwell a native `<select>` allows. */
const TYPE_AHEAD_RESET_MS = 800;

function optionId(agencyId: number): string {
  return `agency-switcher-option-${agencyId}`;
}

function freshnessLabel(t: TFunction, level: FreshnessLevel, days: number | null): string {
  if (level === "unknown" || days === null) return t("agency_switcher.freshness_unknown");
  if (level === "current") return t("agency_switcher.freshness_current");
  return t("common.rel_days_ago", { count: days });
}

/** One row of the listbox. A component rather than a function called during
 *  render: the select handler reaches the switcher's refs, and a plain call
 *  would make that a ref access during the parent's render. */
function AgencyOption({
  agency,
  active,
  isCurrent,
  onSelect,
  onHover,
}: {
  agency: Agency;
  active: boolean;
  isCurrent: boolean;
  onSelect: () => void;
  onHover: () => void;
}) {
  const { t } = useTranslation();
  const { level, days } = agencyFreshness(agency.latest_data_date);
  const freshness = freshnessLabel(t, level, days);
  return (
    <div
      id={optionId(agency.agency_id)}
      role="option"
      aria-selected={active}
      aria-current={isCurrent ? "true" : undefined}
      aria-label={t("agency_switcher.option_aria", { agency: agency.agency_name, freshness })}
      className="agency-switcher-option"
      // Out of the Tab sequence: the listbox owns keyboard focus and the
      // active option is named by aria-activedescendant. Still a real
      // activation target for a screen reader user who navigates onto the
      // row itself.
      tabIndex={-1}
      onClick={onSelect}
      onKeyDown={onActivateKey(onSelect)}
      onMouseEnter={onHover}
    >
      <span className="agency-switcher-option-name">{agency.agency_name}</span>
      <span className={`agency-switcher-pill agency-switcher-pill--${level}`}>{freshness}</span>
    </div>
  );
}

/**
 * The agency switcher that lives in the app's top chrome: the current agency
 * plus a listbox of every other one, recently visited first, each carrying
 * how fresh its data is.
 *
 * Switching is a change of subject, not of view — `buildAgencySwitchPath`
 * keeps the tab and the filter context, so the same question carries over to
 * the new agency.
 *
 * `onSwitch` exists because the confirmation has to outlive this component:
 * on mobile the switcher is inside the "more" sheet, which closes on the
 * switch, so the live region announcing it belongs to the chrome above
 * rather than to the popover that triggered it.
 */
export function AgencySwitcher({ onSwitch }: { onSwitch: (agency: Agency) => void }) {
  const { t } = useTranslation();
  const { data: agencies, isLoading } = useAgencies();
  const navigate = useNavigate();
  const tabMatch = useMatch("/agencies/:agencyId/:tab/*");
  const currentId = useAgencyId();
  const [ctx] = useRangeContext();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [recentIds, setRecentIds] = useState<number[]>(readRecentAgencies);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const typeAhead = useRef<{ buffer: string; timer: ReturnType<typeof setTimeout> | undefined }>({
    buffer: "",
    timer: undefined,
  });

  const all = agencies ?? [];
  const { recent, others } = orderAgenciesForSwitcher(all, recentIds);
  const ordered = [...recent, ...others];
  const current = all.find((a) => a.agency_id === currentId);
  const activeOption = ordered[activeIndex];
  const ctxQueryString = ctxToQueryString(ctx);
  const ctxSuffix = ctxQueryString ? `?${ctxQueryString}` : "";

  function openList() {
    // Re-read rather than trust the mounted copy: another tab in the same
    // browser may have switched agencies since this one mounted.
    const storedRecent = readRecentAgencies();
    const grouped = orderAgenciesForSwitcher(all, storedRecent);
    const index = [...grouped.recent, ...grouped.others].findIndex((a) => a.agency_id === currentId);
    setRecentIds(storedRecent);
    setActiveIndex(index === -1 ? 0 : index);
    setOpen(true);
  }

  function closeList() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function switchTo(agency: Agency) {
    setRecentIds(pushRecentAgency(agency.agency_id));
    setOpen(false);
    navigate(buildAgencySwitchPath(agency.agency_id, tabMatch?.params.tab, ctxSuffix));
    triggerRef.current?.focus();
    onSwitch(agency);
  }

  useEffect(() => {
    if (!open) return;
    listRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocPointerDown);
    return () => document.removeEventListener("mousedown", onDocPointerDown);
  }, [open]);

  const handleOpenEvent = useEffectEvent(() => {
    openList();
  });

  useEffect(() => {
    window.addEventListener(AGENCY_SWITCHER_OPEN_EVENT, handleOpenEvent);
    return () => window.removeEventListener(AGENCY_SWITCHER_OPEN_EVENT, handleOpenEvent);
  }, []);

  useEffect(() => {
    const state = typeAhead.current;
    return () => {
      if (state.timer) clearTimeout(state.timer);
    };
  }, []);

  function onListKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeList();
      return;
    }
    if (e.key === "Tab") {
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(ordered.length - 1, i + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(ordered.length - 1);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (activeOption) switchTo(activeOption);
      return;
    }
    if (e.key.length !== 1 || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    const state = typeAhead.current;
    if (state.timer) clearTimeout(state.timer);
    state.buffer += e.key;
    state.timer = setTimeout(() => {
      state.buffer = "";
    }, TYPE_AHEAD_RESET_MS);
    const names = ordered.map((a) => a.agency_name);
    setActiveIndex((i) => typeAheadIndex(names, state.buffer, i));
  }

  if (isLoading) {
    return <span className="agency-switcher-muted">{t("common.loading_agencies")}</span>;
  }

  if (all.length === 0) {
    return <span className="agency-switcher-muted">{t("header.agency_picker_empty")}</span>;
  }

  // One agency is not a choice: a control that can only re-select what is
  // already on screen is noise in the chrome.
  if (all.length === 1) {
    return <span className="agency-switcher-static">{all[0].agency_name}</span>;
  }

  return (
    <div className="agency-switcher" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="agency-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? closeList() : openList())}
      >
        <span className="agency-switcher-trigger-name">
          {current?.agency_name ?? t("header.agency_picker_placeholder")}
        </span>
        <ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" className="agency-switcher-chevron" />
      </button>
      {open && (
        <div className="agency-switcher-popover">
          {/* Focus stays on the listbox itself and the active option is named
              by aria-activedescendant, so type-ahead and the arrow keys reach
              one element instead of a roving tabindex across every row. */}
          <div
            ref={listRef}
            role="listbox"
            tabIndex={-1}
            aria-label={t("agency_switcher.list_aria")}
            aria-activedescendant={activeOption ? optionId(activeOption.agency_id) : undefined}
            className="agency-switcher-list"
            onKeyDown={onListKeyDown}
          >
            {recent.length > 0 && (
              <div role="group" aria-label={t("agency_switcher.group_recent")}>
                <div className="agency-switcher-group-label" aria-hidden="true">
                  {t("agency_switcher.group_recent")}
                </div>
                {recent.map((agency, i) => (
                  <AgencyOption
                    key={agency.agency_id}
                    agency={agency}
                    active={i === activeIndex}
                    isCurrent={agency.agency_id === currentId}
                    onSelect={() => switchTo(agency)}
                    onHover={() => setActiveIndex(i)}
                  />
                ))}
              </div>
            )}
            <div role="group" aria-label={t("agency_switcher.group_all")}>
              {recent.length > 0 && (
                <div className="agency-switcher-group-label" aria-hidden="true">
                  {t("agency_switcher.group_all")}
                </div>
              )}
              {others.map((agency, i) => (
                <AgencyOption
                  key={agency.agency_id}
                  agency={agency}
                  active={recent.length + i === activeIndex}
                  isCurrent={agency.agency_id === currentId}
                  onSelect={() => switchTo(agency)}
                  onHover={() => setActiveIndex(recent.length + i)}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
