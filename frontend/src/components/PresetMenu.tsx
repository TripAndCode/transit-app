import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession } from "../api/auth";

type Preset = { preset_id: number; agency_id: number; name: string; range_ctx: Record<string, any> };

/** Dropdown + save dialog for filter presets; renders a hint when anonymous. */
export function PresetMenu({
  agencyId,
  currentRangeCtx,
  onSelect,
}: {
  agencyId: number;
  currentRangeCtx: Record<string, any>;
  onSelect: (rangeCtx: Record<string, any>) => void;
}) {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  const { data: presets } = useQuery({
    queryKey: ["presets", agencyId],
    queryFn: async (): Promise<Preset[]> => {
      const r = await fetch(`/api/me/presets?agency_id=${agencyId}`, { credentials: "include" });
      if (!r.ok) throw new Error(`/api/me/presets ${r.status}`);
      return r.json();
    },
    enabled: !!session,
  });

  const create = useMutation({
    mutationFn: async (n: string) => {
      const r = await fetch("/api/me/presets", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agency_id: agencyId, name: n, range_ctx: currentRangeCtx }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `POST ${r.status}`);
      }
    },
    onSuccess: () => {
      setOpen(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["presets", agencyId] });
    },
  });

  if (!session) {
    return (
      <span title="ログインで保存" style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
        プリセット
      </span>
    );
  }

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <select
        onChange={(e) => {
          const p = presets?.find((x) => String(x.preset_id) === e.target.value);
          if (p) onSelect(p.range_ctx);
        }}
        defaultValue=""
        style={{ marginRight: 8 }}
      >
        <option value="" disabled>プリセット</option>
        {presets?.map((p) => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
      </select>
      <button onClick={() => setOpen(true)} style={{ fontSize: 12, padding: "2px 8px" }}>
        現在の条件を保存
      </button>
      {open && (
        <div style={{ position: "absolute", top: "100%", left: 0, padding: 12,
                       background: "var(--surface-1)", border: "1px solid var(--surface-2)",
                       borderRadius: 4, zIndex: 10 }}>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="プリセット名"
            style={{ display: "block", marginBottom: 8, padding: 4, width: 200 }}
          />
          <button
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate(name.trim())}
          >
            保存
          </button>
          <button onClick={() => setOpen(false)} style={{ marginLeft: 8 }}>キャンセル</button>
          {create.error && (
            <div style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 4 }}>
              {String(create.error)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
