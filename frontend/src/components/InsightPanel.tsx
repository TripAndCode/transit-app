import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Sparkles } from "lucide-react";
import { useSuggestion } from "../api/hooks";

const ENABLED_KEY = "transit.insightPanelEnabled";
const COLLAPSED_KEY = "transit.insightPanelCollapsed";
const SEEN_KEY = "transit.insightPanelSeen";

/** Fail-open (feature off) if localStorage is unavailable — same shape as
 *  Sidebar.tsx's readCollapsedPref. */
function readEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) === "1";
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
 *  already navigated to from this panel isn't repeated this session. */
function readSeen(): string[] {
  try {
    const raw = sessionStorage.getItem(SEEN_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function addSeen(key: string): void {
  try {
    const seen = readSeen();
    if (!seen.includes(key)) {
      sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen, key]));
    }
  } catch {
    /* ignore */
  }
}

export function InsightPanel() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [seen, setSeen] = useState(readSeen);

  const enabled = readEnabled();
  const suggestion = useSuggestion(id, seen);

  if (!enabled) return null;

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    writeCollapsed(next);
  }

  function handleView() {
    const data = suggestion.data;
    if (!data || id == null) return;
    const key = `${data.report_type}:${data.route_code}`;
    addSeen(key);
    setSeen(readSeen());
    navigate(`/agencies/${id}/analysis/${data.report_type}?routes=${encodeURIComponent(data.route_code)}`);
  }

  return (
    <div
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
          {!suggestion.data && !suggestion.isPending && (
            <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("insight_panel.no_signal")}</p>
          )}
          {suggestion.data && (
            <div>
              <p style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.6 }}>
                {suggestion.data.reason_text}
              </p>
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
