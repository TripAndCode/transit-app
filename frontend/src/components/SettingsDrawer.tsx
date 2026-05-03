import { useEffect, useState } from "react";

type Props = { open: boolean; onClose: () => void };

export function SettingsDrawer({ open, onClose }: Props) {
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (open) setApiKey(localStorage.getItem("api_key") ?? "");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function save() {
    if (apiKey) localStorage.setItem("api_key", apiKey);
    else localStorage.removeItem("api_key");
    onClose();
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.2)",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "absolute",
          top: 0,
          right: 0,
          bottom: 0,
          width: 360,
          background: "var(--bg-surface)",
          padding: 24,
          boxShadow: "-4px 0 16px rgba(0,0,0,0.06)",
        }}
      >
        <h3 style={{ marginTop: 0 }}>設定</h3>
        <label style={{ display: "block", marginTop: 16 }}>
          <div style={{ marginBottom: 4, color: "var(--text-secondary)", fontSize: 13 }}>
            API キー (Pro 利用時)
          </div>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="未設定"
            style={{ width: "100%" }}
          />
        </label>
        <div style={{ display: "flex", gap: 8, marginTop: 24, justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onClose}
            style={{ background: "transparent", border: "1px solid var(--border-subtle)", padding: "6px 12px", borderRadius: 4 }}
          >
            キャンセル
          </button>
          <button
            type="button"
            onClick={save}
            style={{ background: "var(--accent)", color: "#fff", border: "none", padding: "6px 14px", borderRadius: 4 }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
