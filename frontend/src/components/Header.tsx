import { Link, useSearchParams } from "react-router-dom";
import { useState } from "react";
import { AgencyPicker } from "./AgencyPicker";
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
      <Link
        to="/"
        style={{
          textDecoration: "none",
          color: "var(--text-primary)",
          display: "flex",
          flexDirection: "column",
          lineHeight: 1.1,
        }}
      >
        <h1
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 600,
            fontSize: 20,
            margin: 0,
            letterSpacing: "0.01em",
          }}
        >
          遅延ダッシュボード
        </h1>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 11,
            color: "var(--text-tertiary)",
            marginTop: 2,
            letterSpacing: "0.04em",
          }}
        >
          リアルタイム × 時刻表
        </span>
      </Link>
      <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
        <AgencyPicker />
      </div>
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
