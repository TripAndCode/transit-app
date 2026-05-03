import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { useRoutes } from "../api/hooks";
import {
  useRangeContext,
  type DowFilter,
  type ServiceFilter,
  type TimeBand,
} from "../api/rangeContext";

const DOW_OPTIONS: { value: DowFilter; label: string }[] = [
  { value: "all", label: "全曜日" },
  { value: "weekday", label: "平日" },
  { value: "weekend", label: "土日祝" },
];

const SERVICE_OPTIONS: { value: ServiceFilter; label: string }[] = [
  { value: "all", label: "全種別" },
  { value: "平日", label: "平日" },
  { value: "土日祝", label: "土日祝" },
];

const TIME_BAND_OPTIONS: { value: TimeBand; label: string }[] = [
  { value: "all", label: "全時間帯" },
  { value: "morning", label: "朝(05-09)" },
  { value: "forenoon", label: "午前(09-12)" },
  { value: "noon", label: "昼(12-14)" },
  { value: "afternoon", label: "午後(14-17)" },
  { value: "evening", label: "夕(17-20)" },
  { value: "night", label: "夜(20-24)" },
  { value: "late_night", label: "深夜(00-05)" },
];

const pill = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "var(--bg-surface)",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
  borderRadius: 999,
  padding: "4px 12px",
  fontSize: 12,
  cursor: "pointer",
});

type Draft = {
  dow: DowFilter;
  time_band: TimeBand;
  service: ServiceFilter;
  routes: string[];
};

export function TabFilterBar() {
  const [ctx, setCtx] = useRangeContext();
  const [draft, setDraft] = useState<Draft>({
    dow: ctx.dow,
    time_band: ctx.time_band,
    service: ctx.service,
    routes: ctx.routes,
  });

  useEffect(() => {
    setDraft({ dow: ctx.dow, time_band: ctx.time_band, service: ctx.service, routes: ctx.routes });
  }, [ctx.dow, ctx.time_band, ctx.service, ctx.routes]);

  const dirty =
    draft.dow !== ctx.dow ||
    draft.time_band !== ctx.time_band ||
    draft.service !== ctx.service ||
    draft.routes.join(",") !== ctx.routes.join(",");

  const filtered =
    draft.dow !== "all" ||
    draft.time_band !== "all" ||
    draft.service !== "all" ||
    draft.routes.length > 0;

  function apply() {
    setCtx({
      dow: draft.dow,
      time_band: draft.time_band,
      service: draft.service,
      routes: draft.routes.length > 0 ? draft.routes : null,
    });
  }

  function reset() {
    setDraft({ dow: "all", time_band: "all", service: "all", routes: [] });
    setCtx({ dow: "all", time_band: "all", service: "all", routes: null });
  }

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
        alignItems: "center",
        padding: "8px 0 16px",
        borderBottom: "1px solid var(--border-soft)",
        marginBottom: 16,
      }}
    >
      <PillGroup label="曜日">
        {DOW_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setDraft((d) => ({ ...d, dow: o.value }))}
            style={pill(draft.dow === o.value)}
          >
            {o.label}
          </button>
        ))}
      </PillGroup>

      <PillGroup label="種別">
        {SERVICE_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setDraft((d) => ({ ...d, service: o.value }))}
            style={pill(draft.service === o.value)}
          >
            {o.label}
          </button>
        ))}
      </PillGroup>

      <PillGroup label="時間帯">
        {TIME_BAND_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setDraft((d) => ({ ...d, time_band: o.value }))}
            style={pill(draft.time_band === o.value)}
          >
            {o.label}
          </button>
        ))}
      </PillGroup>

      <RoutesPicker selected={draft.routes} onChange={(routes) => setDraft((d) => ({ ...d, routes }))} />

      <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        {filtered && (
          <button
            type="button"
            onClick={reset}
            style={{
              background: "transparent",
              border: "1px solid var(--border-subtle)",
              borderRadius: 4,
              padding: "4px 12px",
              fontSize: 12,
              color: "var(--text-secondary)",
            }}
          >
            リセット
          </button>
        )}
        <button
          type="button"
          onClick={apply}
          disabled={!dirty}
          style={{
            background: dirty ? "var(--accent)" : "var(--bg-soft)",
            border: "none",
            color: dirty ? "#fff" : "var(--text-tertiary)",
            borderRadius: 4,
            padding: "4px 14px",
            fontSize: 12,
            cursor: dirty ? "pointer" : "not-allowed",
          }}
        >
          適用
        </button>
      </div>
    </div>
  );
}

function PillGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{label}</span>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{children}</div>
    </>
  );
}

function RoutesPicker({ selected, onChange }: { selected: string[]; onChange: (v: string[]) => void }) {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { data } = useRoutes(id);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const options = useMemo(() => {
    if (!data) return [];
    const seen = new Set<string>();
    const out: { code: string; label: string }[] = [];
    for (const r of data) {
      if (!r.route_code || seen.has(r.route_code)) continue;
      seen.add(r.route_code);
      out.push({ code: r.route_code, label: r.route_short_name || r.route_id });
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [data]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q) || o.code.includes(q));
  }, [options, filter]);

  function toggle(code: string) {
    onChange(selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code]);
  }

  const summary =
    selected.length === 0
      ? "全系統"
      : selected.length === 1
        ? options.find((o) => o.code === selected[0])?.label || selected[0]
        : `${selected.length}系統`;

  return (
    <>
      <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>系統</span>
      <div ref={ref} style={{ position: "relative" }}>
        <button type="button" onClick={() => setOpen((v) => !v)} style={pill(selected.length > 0)}>
          {summary} ▾
        </button>
        {open && (
          <div
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              left: 0,
              minWidth: 280,
              maxHeight: 360,
              background: "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
              zIndex: 30,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <input
              autoFocus
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="検索 (名前/コード)"
              style={{ width: "100%", border: "none", borderBottom: "1px solid var(--border-soft)", borderRadius: 0 }}
            />
            <div style={{ overflowY: "auto", flex: 1 }}>
              {filtered.slice(0, 200).map((o) => {
                const on = selected.includes(o.code);
                return (
                  <div
                    key={o.code}
                    onClick={() => toggle(o.code)}
                    style={{
                      padding: "6px 12px",
                      cursor: "pointer",
                      background: on ? "var(--accent-soft)" : "transparent",
                      fontSize: 13,
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <input type="checkbox" checked={on} readOnly />
                    <span style={{ flex: 1 }}>
                      {o.label} <span style={{ color: "var(--text-tertiary)" }}>({o.code})</span>
                    </span>
                  </div>
                );
              })}
              {filtered.length === 0 && (
                <div style={{ padding: 12, color: "var(--text-tertiary)", fontSize: 13 }}>該当なし</div>
              )}
            </div>
            {selected.length > 0 && (
              <div
                style={{
                  borderTop: "1px solid var(--border-soft)",
                  padding: 8,
                  display: "flex",
                  justifyContent: "flex-end",
                }}
              >
                <button
                  type="button"
                  onClick={() => onChange([])}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--text-secondary)",
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  選択解除
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
