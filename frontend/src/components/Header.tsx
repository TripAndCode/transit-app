import { Link, useSearchParams } from "react-router-dom";
import { useState } from "react";
import { AgencyPicker } from "./AgencyPicker";
import { RangeBadge } from "./RangeBadge";
import { SettingsDrawer } from "./SettingsDrawer";
import { AgencyForm } from "../admin/AgencyForm";

export function Header() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [params] = useSearchParams();
  const isAdmin = params.get("admin") === "1";

  return (
    <header
      style={{
        height: 56,
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-soft)",
        display: "flex",
        alignItems: "center",
        padding: "0 24px",
        gap: 24,
      }}
    >
      <Link to="/" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
        遅延ダッシュボード
      </Link>
      <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
        <AgencyPicker />
      </div>
      <RangeBadge />
      <div style={{ display: "flex", gap: 8 }}>
        {isAdmin && (
          <button
            type="button"
            onClick={() => setAdminOpen(true)}
            style={{
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              padding: "6px 14px",
              borderRadius: 4,
            }}
          >
            + 新規事業者
          </button>
        )}
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          aria-label="設定"
          style={{
            background: "transparent",
            border: "1px solid var(--border-subtle)",
            padding: "6px 10px",
            borderRadius: 4,
          }}
        >
          ⚙
        </button>
      </div>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {adminOpen && <AgencyForm onClose={() => setAdminOpen(false)} />}
    </header>
  );
}
