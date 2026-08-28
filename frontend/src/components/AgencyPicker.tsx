import { useEffect, useRef, useState } from "react";
import { useMatch, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";
import type { Agency } from "../api/types";
import { onActivateKey } from "../utils/a11y";

// Module-scope pure function rather than an in-render IIFE — see
// eslint.config.js's manual-memoization ban comment for why this shape is
// preferred over an inline immediately-invoked function expression.
function filterAgencies(agencies: Agency[] | undefined, filter: string): Agency[] {
  if (!agencies) return [];
  const q = filter.trim().toLowerCase();
  if (!q) return agencies;
  return agencies.filter((a) => a.agency_name.toLowerCase().includes(q));
}

export function AgencyPicker() {
  const { t } = useTranslation();
  const { data: agencies, isLoading } = useAgencies();
  const { agencyId } = useParams();
  const navigate = useNavigate();
  const tabMatch = useMatch("/agencies/:agencyId/:tab/*");
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const currentId = agencyId ? Number(agencyId) : null;
  const current = agencies?.find((a) => a.agency_id === currentId);

  // close on outside click
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filtered = filterAgencies(agencies, filter);

  if (isLoading) {
    return <span style={{ color: "var(--text-tertiary)" }}>{t("common.loading_agencies")}</span>;
  }

  if (!agencies || agencies.length === 0) {
    return <span style={{ color: "var(--text-tertiary)" }}>{t("header.agency_picker_empty")}</span>;
  }

  // Single agency: static label, no dropdown
  if (agencies.length === 1) {
    return <strong>{agencies[0].agency_name}</strong>;
  }

  function selectAgency(id: number) {
    setOpen(false);
    setFilter("");
    const tab = tabMatch?.params.tab ?? "map";
    navigate(`/agencies/${id}/${tab}`);
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: "var(--bg-surface)",
          color: "var(--text-primary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          padding: "6px 12px",
          minWidth: 200,
          textAlign: "left",
        }}
      >
        {current?.agency_name ?? t("header.agency_picker_placeholder")}
        <span style={{ float: "right", color: "var(--text-tertiary)" }}>▾</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            zIndex: 20,
            overflow: "hidden",
          }}
        >
          <input
            // eslint-disable-next-line jsx-a11y/no-autofocus -- search field of a just-opened picker dropdown; focusing it is the expected UX
            autoFocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t("common.search_placeholder")}
            style={{ width: "100%", border: "none", borderBottom: "1px solid var(--border-soft)", borderRadius: 0 }}
          />
          <div role="listbox" style={{ maxHeight: 280, overflowY: "auto" }}>
            {filtered.map((a) => (
              <div
                key={a.agency_id}
                role="option"
                aria-selected={a.agency_id === currentId}
                tabIndex={0}
                onClick={() => selectAgency(a.agency_id)}
                onKeyDown={onActivateKey(() => selectAgency(a.agency_id))}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  background: a.agency_id === currentId ? "var(--accent-soft)" : "transparent",
                }}
              >
                {a.agency_name}
              </div>
            ))}
            {filtered.length === 0 && (
              <div style={{ padding: 12, color: "var(--text-tertiary)" }}>{t("common.no_match")}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
