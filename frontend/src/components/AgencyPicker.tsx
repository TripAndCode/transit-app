import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useAgencies } from "../api/hooks";

export function AgencyPicker() {
  const { data: agencies, isLoading } = useAgencies();
  const { agencyId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const currentId = agencyId ? Number(agencyId) : null;
  const current = agencies?.find((a) => a.agency_id === currentId);

  // auto-redirect to first agency once data arrives
  useEffect(() => {
    if (!agencies || agencies.length === 0) return;
    if (currentId == null) {
      navigate(`/agencies/${agencies[0].agency_id}/map`, { replace: true });
    }
  }, [agencies, currentId, navigate]);

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
    return <span style={{ color: "var(--text-tertiary)" }}>事業者読み込み中...</span>;
  }

  if (!agencies || agencies.length === 0) {
    return <span style={{ color: "var(--text-tertiary)" }}>事業者が登録されていません</span>;
  }

  // Single agency: static label, no dropdown
  if (agencies.length === 1) {
    return <strong>{agencies[0].agency_name}</strong>;
  }

  function selectAgency(id: number) {
    setOpen(false);
    setFilter("");
    // preserve current tab path; default to map
    const tab = location.pathname.match(/\/agencies\/\d+\/([^/]+)/)?.[1] ?? "map";
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
        {current?.agency_name ?? "事業者を選択"}
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
            placeholder="検索..."
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
              <div style={{ padding: 12, color: "var(--text-tertiary)" }}>該当なし</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
