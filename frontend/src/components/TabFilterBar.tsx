import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { useRoutes } from "../api/hooks";
import {
  DEFAULT_RANGE_DAYS,
  isoDaysAgo,
  todayISO,
  useRangeContext,
  type DowFilter,
  type ServiceFilter,
  type TimeBand,
} from "../api/rangeContext";
import { PresetMenu } from "./PresetMenu";
import { RangeBadge } from "./RangeBadge";
import { RoutesPicker } from "./RoutesPicker";

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

const TIME_BAND_LABEL: Record<TimeBand, string> = Object.fromEntries(
  TIME_BAND_OPTIONS.map((o) => [o.value, o.label]),
) as Record<TimeBand, string>;

const pill = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "var(--bg-surface)",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-soft)"}`,
  borderRadius: 999,
  padding: "5px 12px",
  fontSize: 12,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
  transition: "all var(--transition)",
});

const groupLabel: CSSProperties = {
  fontSize: 11,
  color: "var(--text-tertiary)",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  marginBottom: 6,
  display: "block",
};

type Draft = {
  dow: DowFilter;
  time_band: TimeBand;
  service: ServiceFilter;
  routes: string[];
};

export function TabFilterBar() {
  const [ctx, setCtx] = useRangeContext();
  const agencyIdNum = useAgencyId();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<Draft>({
    dow: ctx.dow,
    time_band: ctx.time_band,
    service: ctx.service,
    routes: ctx.routes,
  });
  const { data: routes } = useRoutes(agencyIdNum);

  useEffect(() => {
    setDraft({ dow: ctx.dow, time_band: ctx.time_band, service: ctx.service, routes: ctx.routes });
  }, [ctx.dow, ctx.time_band, ctx.service, ctx.routes]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const dirty =
    draft.dow !== ctx.dow ||
    draft.time_band !== ctx.time_band ||
    draft.service !== ctx.service ||
    draft.routes.join(",") !== ctx.routes.join(",");

  const activeCount =
    (ctx.dow !== "all" ? 1 : 0) +
    (ctx.time_band !== "all" ? 1 : 0) +
    (ctx.service !== "all" ? 1 : 0) +
    (ctx.routes.length > 0 ? 1 : 0);

  const routeNameMap = useMemo(() => {
    const m = new Map<string, string>();
    if (routes) for (const r of routes) {
      if (r.route_code) m.set(r.route_code, r.route_short_name || r.route_id);
    }
    return m;
  }, [routes]);

  // route_short_name → list of route_codes that share it.
  // A single display name like "K37 観光通り線" maps to several codes
  // (different operating variants); the picker can collapse-select all
  // of them, and the chips below merge accordingly.
  const groupCodesByName = useMemo(() => {
    const m = new Map<string, string[]>();
    if (routes) for (const r of routes) {
      if (!r.route_code || !r.route_short_name) continue;
      const arr = m.get(r.route_short_name) || [];
      arr.push(r.route_code);
      m.set(r.route_short_name, arr);
    }
    return m;
  }, [routes]);

  // Decide which selected route codes collapse into a single "by-name" chip
  // and which stand alone. A group collapses only when *all* its codes are
  // selected — partial selection still shows per-code chips so the user
  // doesn't lose visibility of what's actually filtered.
  type ChipSpec =
    | { kind: "name"; name: string; codes: string[] }
    | { kind: "code"; code: string };
  const routeChips = useMemo<ChipSpec[]>(() => {
    const sel = new Set(ctx.routes);
    const used = new Set<string>();
    const chips: ChipSpec[] = [];
    for (const [name, codes] of groupCodesByName) {
      if (codes.length > 1 && codes.every((c) => sel.has(c))) {
        chips.push({ kind: "name", name, codes });
        for (const c of codes) used.add(c);
      }
    }
    for (const code of ctx.routes) {
      if (!used.has(code)) chips.push({ kind: "code", code });
    }
    return chips;
  }, [ctx.routes, groupCodesByName]);

  function apply() {
    setCtx({
      dow: draft.dow,
      time_band: draft.time_band,
      service: draft.service,
      routes: draft.routes.length > 0 ? draft.routes : null,
    });
    setOpen(false);
  }

  function reset() {
    // Reset includes the date range — drilldowns from the trend heatmap set
    // from=to=<single day>; without resetting the dates here, "全てクリア"
    // leaves the user stuck on a one-day window.
    const cleared: Draft = { dow: "all", time_band: "all", service: "all", routes: [] };
    setDraft(cleared);
    setCtx({
      from: isoDaysAgo(DEFAULT_RANGE_DAYS - 1),
      to: todayISO(),
      dow: "all",
      time_band: "all",
      service: "all",
      routes: null,
    });
  }

  function clearChip(kind: "dow" | "time_band" | "service" | "route", value?: string) {
    if (kind === "dow") setCtx({ dow: "all" });
    if (kind === "time_band") setCtx({ time_band: "all" });
    if (kind === "service") setCtx({ service: "all" });
    if (kind === "route" && value) {
      const remaining = ctx.routes.filter((r) => r !== value);
      setCtx({ routes: remaining.length > 0 ? remaining : null });
    }
  }

  function clearNameChip(codes: string[]) {
    const drop = new Set(codes);
    const remaining = ctx.routes.filter((r) => !drop.has(r));
    setCtx({ routes: remaining.length > 0 ? remaining : null });
  }

  return (
    <div
      ref={ref}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
        marginBottom: 16,
        position: "relative",
      }}
    >
      <RangeBadge />
      {agencyIdNum !== null && (
        <PresetMenu
          agencyId={agencyIdNum}
          currentRangeCtx={ctx as unknown as Record<string, unknown>}
          onSelect={(rc) => setCtx({ ...ctx, ...rc } as typeof ctx)}
        />
      )}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: activeCount > 0 ? "var(--accent)" : "var(--bg-surface)",
          color: activeCount > 0 ? "#fff" : "var(--text-primary)",
          border: `1px solid ${activeCount > 0 ? "var(--accent)" : "var(--border-subtle)"}`,
          borderRadius: 8,
          padding: "8px 16px",
          fontSize: 14,
          fontWeight: 600,
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
          boxShadow: activeCount > 0 ? "0 1px 3px rgba(91,108,173,0.30)" : "none",
          transition: "all var(--transition)",
        }}
      >
        <span aria-hidden style={{ fontSize: 16 }}>⚙</span>
        フィルタ
        {activeCount > 0 && (
          <span
            style={{
              background: "rgba(255,255,255,0.25)",
              color: "#fff",
              fontSize: 12,
              borderRadius: 999,
              padding: "1px 8px",
              fontWeight: 700,
              minWidth: 18,
              textAlign: "center",
            }}
          >
            {activeCount}
          </span>
        )}
        <span style={{ opacity: 0.7 }}>▾</span>
      </button>

      {/* Inline chips of active filters with × to clear individually */}
      {ctx.dow !== "all" && (
        <Chip label={`曜日: ${dowLabel(ctx.dow)}`} onClear={() => clearChip("dow")} />
      )}
      {ctx.service !== "all" && (
        <Chip label={`種別: ${ctx.service}`} onClear={() => clearChip("service")} />
      )}
      {ctx.time_band !== "all" && (
        <Chip label={`時間帯: ${TIME_BAND_LABEL[ctx.time_band]}`} onClear={() => clearChip("time_band")} />
      )}
      {routeChips.map((c) =>
        c.kind === "name" ? (
          <Chip
            key={`name:${c.name}`}
            label={`${c.name} (${c.codes.length}系統)`}
            onClear={() => clearNameChip(c.codes)}
          />
        ) : (
          <Chip
            key={c.code}
            label={routeNameMap.get(c.code) ? `${routeNameMap.get(c.code)} (${c.code})` : `系統${c.code}`}
            onClear={() => clearChip("route", c.code)}
          />
        ),
      )}
      {activeCount > 0 && (
        <button
          type="button"
          onClick={reset}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-tertiary)",
            fontSize: 12,
            padding: "4px 6px",
            cursor: "pointer",
            textDecoration: "underline",
          }}
        >
          全てクリア
        </button>
      )}

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 50,
            width: 480,
            maxWidth: "calc(100vw - 48px)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.10)",
            padding: 18,
          }}
        >
          <div style={{ marginBottom: 14 }}>
            <span style={groupLabel}>曜日</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <span style={groupLabel}>種別 (GTFS)</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <span style={groupLabel}>時間帯</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <span style={groupLabel}>系統</span>
            <RoutesPicker
              selected={draft.routes}
              onChange={(routes) => setDraft((d) => ({ ...d, routes }))}
            />
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border-soft)" }}>
            <button
              type="button"
              onClick={reset}
              style={{
                background: "transparent",
                border: "1px solid var(--border-soft)",
                borderRadius: 4,
                padding: "6px 14px",
                fontSize: 13,
                color: "var(--text-secondary)",
                cursor: "pointer",
              }}
            >
              ↺ リセット
            </button>
            <button
              type="button"
              onClick={apply}
              disabled={!dirty}
              style={{
                background: dirty ? "var(--accent)" : "var(--bg-soft)",
                color: dirty ? "#fff" : "var(--text-tertiary)",
                border: "none",
                borderRadius: 4,
                padding: "6px 18px",
                fontSize: 13,
                fontWeight: 500,
                cursor: dirty ? "pointer" : "not-allowed",
                boxShadow: dirty ? "0 1px 2px rgba(91,108,173,0.25)" : "none",
              }}
            >
              ✓ 適用
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function useAgencyId(): number | null {
  const { agencyId } = useParams();
  return agencyId ? Number(agencyId) : null;
}

function dowLabel(d: DowFilter): string {
  return d === "weekday" ? "平日" : d === "weekend" ? "土日祝" : "全曜日";
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: "var(--accent-soft)",
        color: "var(--accent)",
        border: "1px solid var(--accent)",
        borderRadius: 999,
        padding: "3px 10px 3px 12px",
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      {label}
      <button
        type="button"
        onClick={onClear}
        aria-label={`${label} を解除`}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          padding: 0,
          cursor: "pointer",
          fontSize: 14,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </span>
  );
}

