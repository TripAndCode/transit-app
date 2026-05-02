import { Link } from "react-router-dom";
import { useState } from "react";
import { AgencyPicker } from "./AgencyPicker";
import { SettingsDrawer } from "./SettingsDrawer";

export function Header() {
  const [open, setOpen] = useState(false);

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
      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          onClick={() => setOpen(true)}
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
      <SettingsDrawer open={open} onClose={() => setOpen(false)} />
    </header>
  );
}
