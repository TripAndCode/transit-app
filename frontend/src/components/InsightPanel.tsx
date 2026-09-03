import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Sparkles } from "lucide-react";
import { useSuggestion } from "../api/hooks";
import { delayColor } from "../styles/tokens";

// Map the backend's binary severity onto the existing delay warm ramp
// (CLAUDE.md: "Severity uses the existing warm ramp") via representative
// minute values landing in delayBand()'s "severe" vs "ok" tiers, rather
// than inventing new colors just for this panel.
function severityColor(severity: "notable" | "normal"): string {
  return delayColor(severity === "notable" ? 6 : 0);
}

const ENABLED_KEY = "transit.insightPanelEnabled";
const COLLAPSED_KEY = "transit.insightPanelCollapsed";
const SEEN_KEY = "transit.insightPanelSeen";

/** Defaults on in dev builds (same `import.meta.env.DEV` gate as
 *  Sidebar.tsx's PROTOTYPE section) so dogfooding needs no manual setup;
 *  a real deploy stays opt-in-only. An explicit localStorage preference
 *  (set either way via devtools) always wins over that default. Fail-open
 *  to off if localStorage is unavailable — same shape as
 *  Sidebar.tsx's readCollapsedPref. */
function readEnabled(): boolean {
  try {
    const stored = localStorage.getItem(ENABLED_KEY);
    if (stored != null) return stored === "1";
    return import.meta.env.DEV;
  } catch {
    return false;
  }
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}

/** Session-only "already shown" pathway keys, so a route/reason the user
 *  already navigated to from this panel isn't repeated this session.
 *  Namespaced per agency -- paired with `key={agencyId}` at the mount site
 *  in AnalysisTab.tsx so switching agencies both remounts this component
 *  (fresh `seen` state) and reads/writes a different storage key, instead
 *  of one agency's dismissed suggestion silently suppressing a same-numbered
 *  route on another agency for the rest of the browser session. */
function seenStorageKey(agencyId: number): string {
  return `${SEEN_KEY}.${agencyId}`;
}

function readSeen(agencyId: number): string[] {
  try {
    const raw = sessionStorage.getItem(seenStorageKey(agencyId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    // Guard the parse result's shape, not just catch JSON syntax errors --
    // valid-but-wrong-shaped JSON (e.g. "{}") parses without throwing, and
    // a non-array here would crash `exclude.join(",")` in useSuggestion.
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function addSeen(agencyId: number, key: string): void {
  try {
    const seen = readSeen(agencyId);
    if (!seen.includes(key)) {
      sessionStorage.setItem(seenStorageKey(agencyId), JSON.stringify([...seen, key]));
    }
  } catch {
    /* ignore */
  }
}

export function InsightPanel({ className }: { className?: string } = {}) {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [seen, setSeen] = useState<string[]>(() => (id != null ? readSeen(id) : []));

  const enabled = readEnabled();
  // Also gated on !collapsed: a collapsed panel has nowhere to show a
  // suggestion, so polling it every refetchInterval would just be wasted
  // backend load for a rail the user has explicitly hidden.
  const suggestion = useSuggestion(enabled && !collapsed ? id : null, seen);

  if (!enabled) return null;

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      writeCollapsed(next);
      return next;
    });
  }

  function handleView() {
    const data = suggestion.data;
    if (!data || id == null) return;
    const key = `${data.report_type}:${data.route_code}`;
    addSeen(id, key);
    setSeen(readSeen(id));
    // Pin from/to to the window this suggestion actually evaluated (rather
    // than the user's ambient Analysis tab filter, e.g. useRangeContext's
    // 30-day default) so the click-through lands exactly where the reason
    // text's numbers are visible.
    const qs = new URLSearchParams({
      routes: data.route_code,
      from: data.from_date,
      to: data.to_date,
    });
    navigate(`/agencies/${id}/analysis/${data.report_type}?${qs.toString()}`);
  }

  return (
    <div
      className={className}
      style={{
        width: collapsed ? 40 : 260,
        flexShrink: 0,
        borderLeft: "1px solid var(--border-subtle)",
        padding: collapsed ? "12px 8px" : "12px 16px",
        transition: "width var(--transition)",
      }}
    >
      <button
        type="button"
        onClick={toggleCollapsed}
        aria-label={collapsed ? t("insight_panel.expand_aria") : t("insight_panel.collapse_aria")}
        style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-tertiary)" }}
      >
        {collapsed ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
      </button>
      {!collapsed && (
        <>
          <h4
            style={{
              margin: "8px 0 12px",
              fontSize: 13,
              display: "flex",
              alignItems: "center",
              gap: 6,
              color: "var(--text-secondary)",
            }}
          >
            <Sparkles size={14} strokeWidth={1.75} />
            {t("insight_panel.title")}
          </h4>
          {suggestion.error ? (
            <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("insight_panel.load_error")}</p>
          ) : (
            !suggestion.data &&
            !suggestion.isPending && (
              <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("insight_panel.no_signal")}</p>
            )
          )}
          {!suggestion.error && suggestion.data && (
            <div>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 6, marginBottom: 8 }}>
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    marginTop: 5,
                    flexShrink: 0,
                    background: severityColor(suggestion.data.severity),
                  }}
                />
                <p style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.6, margin: 0 }}>
                  {suggestion.data.reason_text}
                </p>
              </div>
              <button
                type="button"
                onClick={handleView}
                style={{
                  fontSize: 12,
                  padding: "6px 12px",
                  borderRadius: "var(--radius-lg)",
                  border: "1px solid var(--border-subtle)",
                  background: "var(--accent-soft)",
                  cursor: "pointer",
                }}
              >
                {t("insight_panel.view_button")}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
