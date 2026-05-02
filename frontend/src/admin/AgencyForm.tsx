import { useEffect, useState } from "react";
import { useCreateAgency } from "../api/hooks";

type Props = { onClose: () => void };

export function AgencyForm({ onClose }: Props) {
  const [name, setName] = useState("");
  const [feed, setFeed] = useState("");
  const [staticUrl, setStaticUrl] = useState("");
  const create = useCreateAgency();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name || !feed) return;
    try {
      await create.mutateAsync({
        agency_name: name,
        feed_url: feed,
        static_url: staticUrl || null,
      });
      onClose();
    } catch {
      // error surfaced via create.error
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.3)",
        zIndex: 200,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="agency-form-title"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        style={{
          background: "var(--bg-surface)",
          padding: 24,
          borderRadius: "var(--radius-lg)",
          width: 480,
          maxWidth: "90vw",
        }}
      >
        <h3 id="agency-form-title" style={{ marginTop: 0 }}>新規事業者登録</h3>
        <Field label="事業者名">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            style={{ width: "100%" }}
          />
        </Field>
        <Field label="GTFS-RT Feed URL">
          <input value={feed} onChange={(e) => setFeed(e.target.value)} required style={{ width: "100%" }} />
        </Field>
        <Field label="GTFS Static URL (任意)">
          <input value={staticUrl} onChange={(e) => setStaticUrl(e.target.value)} style={{ width: "100%" }} />
        </Field>
        {create.error && (
          <div style={{ color: "var(--error-fg)", fontSize: 13, marginTop: 8 }}>
            登録に失敗しました
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ background: "transparent", border: "1px solid var(--border-subtle)", padding: "6px 14px", borderRadius: 4 }}>
            キャンセル
          </button>
          <button type="submit" disabled={create.isPending} style={{ background: "var(--accent)", color: "#fff", border: "none", padding: "6px 16px", borderRadius: 4 }}>
            {create.isPending ? "登録中..." : "登録"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      <div style={{ marginBottom: 4, fontSize: 13, color: "var(--text-secondary)" }}>{label}</div>
      {children}
    </label>
  );
}
