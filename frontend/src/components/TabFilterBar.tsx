import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useRoutes } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { routeDisplayName } from "../api/routeDisplayName";
import {
  useRangeContext,
  type DowFilter,
  type ServiceFilter,
  type TimeBand,
} from "../api/rangeContext";
import { Glossary } from "./Glossary";
import { PresetMenu } from "./PresetMenu";
import { RangeBadge } from "./RangeBadge";
import { RoutesPicker } from "./RoutesPicker";
import { buildTimeBandOptions } from "./timeBandOptions";
import { pill, groupLabel } from "./pillStyles";
import { Z_INDEX } from "../styles/zIndex";


type Draft = {
  dow: DowFilter;
  time_band: TimeBand;
  service: ServiceFilter;
  routes: string[];
};

export function TabFilterBar({ after }: { after?: ReactNode } = {}) {
  const { t } = useTranslation();
  const [ctx, setCtx] = useRangeContext();
  const agencyIdNum = useAgencyId();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [draft, setDraft] = useState<Draft>({
    dow: ctx.dow,
    time_band: ctx.time_band,
    service: ctx.service,
    routes: ctx.routes,
  });
  const { data: routes } = useRoutes(agencyIdNum);

  const dowOptions: { value: DowFilter; label: string }[] = [
    { value: "all", label: t("filters.dow.all") },
    { value: "weekday", label: t("common.service_value.平日") }, // i18n-ignore: query contract
    { value: "weekend", label: t("common.service_value.土日祝") }, // i18n-ignore: query contract
  ];

  const serviceOptions: { value: ServiceFilter; label: string }[] = [
    { value: "all", label: t("filters.service.all") },
    // value stays as the raw JP string (URL query value); only the label is translated
    { value: "平日", label: t("common.service_value.平日") }, // i18n-ignore: query contract
    { value: "土日祝", label: t("common.service_value.土日祝") }, // i18n-ignore: query contract
  ];

  const timeBandOptions = buildTimeBandOptions(t);

  const timeBandLabel = Object.fromEntries(
    timeBandOptions.map((o) => [o.value, o.label]),
  ) as Record<TimeBand, string>;

  const serviceLabel = Object.fromEntries(
    serviceOptions.map((o) => [o.value, o.label]),
  ) as Record<ServiceFilter, string>;

  // Mirror external ctx changes (chip clears, presets, drilldowns) into the
  // popover draft via the render-adjust pattern — same semantics as the old
  // sync-effect but without the extra commit + effect pass.
  const ctxKey = `${ctx.dow}|${ctx.time_band}|${ctx.service}|${ctx.routes.join(",")}`;
  const [prevCtxKey, setPrevCtxKey] = useState(ctxKey);
  if (prevCtxKey !== ctxKey) {
    setPrevCtxKey(ctxKey);
    setDraft({ dow: ctx.dow, time_band: ctx.time_band, service: ctx.service, routes: ctx.routes });
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // The popover has no close control of its own, so without this the only
  // way out for a keyboard user is a click elsewhere on the page. It is not
  // a focus trap -- Tab still leaves it -- so it acts only on a keypress
  // that belongs to it: anything with focus of its own (a dialog opened over
  // the page) owns its own Escape.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (!ref.current?.contains(document.activeElement)) return;
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

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

  // See routeDisplayName's doc comment for the fallback order rationale
  // (shared with useRouteNames.ts to avoid the two diverging again).
  const routeNameMap = new Map<string, string>();
  if (routes) for (const r of routes) {
    if (r.route_code) routeNameMap.set(r.route_code, routeDisplayName(r));
  }

  // route_short_name → list of route_codes that share it.
  // A single display name like "K37 観光通り線" maps to several codes // i18n-ignore: comment
  // (different operating variants); the picker can collapse-select all
  // of them, and the chips below merge accordingly.
  const groupCodesByName = new Map<string, string[]>();
  if (routes) for (const r of routes) {
    if (!r.route_code || !r.route_short_name) continue;
    const arr = groupCodesByName.get(r.route_short_name) || [];
    arr.push(r.route_code);
    groupCodesByName.set(r.route_short_name, arr);
  }

  // Decide which selected route codes collapse into a single "by-name" chip
  // and which stand alone. A group collapses only when *all* its codes are
  // selected — partial selection still shows per-code chips so the user
  // doesn't lose visibility of what's actually filtered.
  type ChipSpec =
    | { kind: "name"; name: string; codes: string[] }
    | { kind: "code"; code: string };
  const selectedRouteCodes = new Set(ctx.routes);
  const usedRouteCodes = new Set<string>();
  const routeChips: ChipSpec[] = [];
  for (const [name, codes] of groupCodesByName) {
    if (codes.length > 1 && codes.every((c) => selectedRouteCodes.has(c))) {
      routeChips.push({ kind: "name", name, codes });
      for (const c of codes) usedRouteCodes.add(c);
    }
  }
  for (const code of ctx.routes) {
    if (!usedRouteCodes.has(code)) routeChips.push({ kind: "code", code });
  }

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
    // from=to=<single day>; without resetting the dates here, "全てクリア" // i18n-ignore: comment
    // leaves the user stuck on a one-day window. Clearing (not hardcoding
    // today's window) lets useDefaultRangeAnchor re-derive the right
    // default — hardcoding today's window here would trap a lagging
    // agency on a guaranteed-empty range with no way back to its real data.
    const cleared: Draft = { dow: "all", time_band: "all", service: "all", routes: [] };
    setDraft(cleared);
    setCtx({
      from: null,
      to: null,
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
    <div ref={ref} style={{ marginBottom: 16, position: "relative" }}>
      {/* The scrolling behavior lives on this inner row, not the outer
          relative-positioned wrapper: the wrapper also anchors the filter
          popover below via `position: absolute`, and giving the wrapper
          itself `overflow-x: auto` would clip that popover's vertical
          overflow too (an auto axis forces the other axis to auto as well,
          per the CSS overflow spec). */}
      <div className="tab-filter-bar-row" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <RangeBadge />
      {agencyIdNum !== null && (
        <PresetMenu
          agencyId={agencyIdNum}
          currentRangeCtx={ctx}
          onSelect={(rc) => setCtx(rc)}
        />
      )}
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        style={{
          background: activeCount > 0 ? "var(--accent)" : "var(--bg-surface)",
          color: activeCount > 0 ? "var(--on-accent)" : "var(--text-primary)",
          border: `1px solid ${activeCount > 0 ? "var(--accent)" : "var(--border-subtle)"}`,
          borderRadius: 8,
          padding: "8px 16px",
          fontSize: 14,
          fontWeight: 600,
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
          boxShadow: activeCount > 0 ? "var(--el-1)" : "none",
          transition: "all var(--transition)",
        }}
      >
        <span aria-hidden style={{ fontSize: 16 }}>⚙</span>
        {t("filters.title")}
        {activeCount > 0 && (
          <span
            style={{
              background: "rgba(255,255,255,0.25)",
              color: "var(--on-accent)",
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
        <Chip label={`${t("filters.dow.label")}: ${dowLabel(ctx.dow, t)}`} onClear={() => clearChip("dow")} />
      )}
      {ctx.service !== "all" && (
        <Chip label={`${t("filters.service.label")}: ${serviceLabel[ctx.service]}`} onClear={() => clearChip("service")} />
      )}
      {ctx.time_band !== "all" && (
        <Chip label={`${t("filters.time_band.label")}: ${timeBandLabel[ctx.time_band]}`} onClear={() => clearChip("time_band")} />
      )}
      {routeChips.map((c) =>
        c.kind === "name" ? (
          <Chip
            key={`name:${c.name}`}
            label={`${c.name} ${t("filters.routes.variant_count", { count: c.codes.length })}`}
            onClear={() => clearNameChip(c.codes)}
          />
        ) : (
          <Chip
            key={c.code}
            label={routeNameMap.get(c.code) ? `${routeNameMap.get(c.code)} (${c.code})` : t("common.route_code_fallback", { code: c.code })}
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
          {t("filters.clear_all")}
        </button>
      )}

      {after && <div style={{ marginLeft: "auto" }}>{after}</div>}
      </div>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: Z_INDEX.popover,
            width: 480,
            maxWidth: "calc(100vw - 48px)",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "var(--el-2)",
            padding: 18,
          }}
        >
          <div style={{ marginBottom: 14 }}>
            <span style={groupLabel}>{t("filters.dow.label")}</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {dowOptions.map((o) => (
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
            <span style={groupLabel}>
              {t("filters.service.label")} (<Glossary term="GTFS" explanation={t("glossary.gtfs")} />)
            </span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {serviceOptions.map((o) => (
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
            <span style={groupLabel}>{t("filters.time_band.label")}</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {timeBandOptions.map((o) => (
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
            <span style={groupLabel}>{t("filters.routes.label")}</span>
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
              {`↺ ${t("common.reset")}`}
            </button>
            <button
              type="button"
              onClick={apply}
              disabled={!dirty}
              style={{
                background: dirty ? "var(--accent)" : "var(--bg-soft)",
                color: dirty ? "var(--on-accent)" : "var(--text-tertiary)",
                border: "none",
                borderRadius: 4,
                padding: "6px 18px",
                fontSize: 13,
                fontWeight: 500,
                cursor: dirty ? "pointer" : "not-allowed",
                boxShadow: dirty ? "var(--el-1)" : "none",
              }}
            >
              {`✓ ${t("common.apply")}`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function dowLabel(d: DowFilter, t: (key: string) => string): string {
  if (d === "weekday") return t("common.service_value.平日"); // i18n-ignore: query contract
  if (d === "weekend") return t("common.service_value.土日祝"); // i18n-ignore: query contract
  return t("filters.dow.all");
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  const { t } = useTranslation();
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
        aria-label={`${label} ${t("filters.chip_remove_suffix")}`}
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

