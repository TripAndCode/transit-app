import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useMatch, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";

export function AgencyPicker() {
  const { t } = useTranslation();
  const { data: agencies, isLoading } = useAgencies();
  const { agencyId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const tabMatch = useMatch("/agencies/:agencyId/:tab/*");
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const currentId = agencyId ? Number(agencyId) : null;
  const current = agencies?.find((a) => a.agency_id === currentId);

  // Auto-redirect to first agency only from the root. Auth/account/admin
  // routes have no :agencyId by design — without this guard the header
  // hijacks every non-agency page back to the map.
  useEffect(() => {
    if (!agencies || agencies.length === 0) return;
    if (currentId != null) return;
    if (location.pathname !== "/") return;
    navigate(`/agencies/${agencies[0].agency_id}/map`, { replace: true });
  }, [agencies, currentId, location.pathname, navigate]);

  // close on outside click
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filtered = useMemo(() => {
    if (!agencies) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return agencies;
    return agencies.filter((a) => a.agency_name.toLowerCase().includes(q));
  }, [agencies, filter]);

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
            autoFocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t("common.search_placeholder")}
            style={{ width: "100%", border: "none", borderBottom: "1px solid var(--border-soft)", borderRadius: 0 }}
          />
          <div style={{ maxHeight: 280, overflowY: "auto" }}>
            {filtered.map((a) => (
              <div
                key={a.agency_id}
                onClick={() => selectAgency(a.agency_id)}
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
