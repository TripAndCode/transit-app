import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = { open: boolean; onClose: () => void };

export function SettingsDrawer({ open, onClose }: Props) {
  const { t } = useTranslation();
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
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.2)",
        zIndex: 100,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("header.settings_title")}
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
        <h3 style={{ marginTop: 0 }}>{t("header.settings_title")}</h3>
        <label style={{ display: "block", marginTop: 16 }}>
          <div style={{ marginBottom: 4, color: "var(--text-secondary)", fontSize: 13 }}>
            {t("header.settings_api_key_label")} <span style={{ color: "var(--text-tertiary)" }}>{t("common.optional_paren")}</span>
          </div>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={t("header.settings_api_key_placeholder")}
            style={{ width: "100%" }}
          />
          <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
            {t("header.settings_api_key_hint")}
          </div>
        </label>
        <div style={{ display: "flex", gap: 8, marginTop: 24, justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onClose}
            style={{ background: "transparent", border: "1px solid var(--border-subtle)", padding: "6px 12px", borderRadius: 4 }}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={save}
            style={{ background: "var(--accent)", color: "#fff", border: "none", padding: "6px 14px", borderRadius: 4 }}
          >
            {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
