import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isoDaysAgo, jstYearMonth, todayISO, toJstISO, useRangeContext, type RangeCtx } from "../api/rangeContext";

type Preset = { key: string; label: string; from: () => string; to: () => string };

/** First day of the current JST month, offset by `offset` months. */
function firstOfMonth(offset: number): string {
  const { year, month } = jstYearMonth(new Date());
  // 00:00 UTC = 09:00 JST same civil day — no anchor needed for JST.
  return toJstISO(new Date(Date.UTC(year, month - 1 + offset, 1)));
}

/** Last day of the current JST month, offset by `offset` months. */
function lastOfMonth(offset: number): string {
  const { year, month } = jstYearMonth(new Date());
  // Day 0 of next month = last day of this one.
  return toJstISO(new Date(Date.UTC(year, month + offset, 0)));
}

function localizedDate(iso: string, language: string): string {
  // yyyy-mm-dd → yyyy/mm/dd (Japan-conventional written form) only for ja;
  // every other locale keeps the ISO dash form used elsewhere in the app
  // (NetworkTab, AdminOpsPage), so switching languages doesn't show two
  // different date conventions for the same kind of value.
  return language.startsWith("ja") ? iso.replaceAll("-", "/") : iso;
}

function isDefault(ctx: RangeCtx): boolean {
  return ctx.from === isoDaysAgo(29) && ctx.to === todayISO();
}

export function RangeBadge() {
  const { t, i18n } = useTranslation();
  const [ctx, setCtx] = useRangeContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = !isDefault(ctx);

  const presets = useMemo<Preset[]>(
    () => [
      { key: "last_7d", label: t("filters.range.last_7d"), from: () => isoDaysAgo(6), to: todayISO },
      { key: "last_30d", label: t("filters.range.last_30d"), from: () => isoDaysAgo(29), to: todayISO },
      { key: "last_90d", label: t("filters.range.last_90d"), from: () => isoDaysAgo(89), to: todayISO },
      { key: "this_month", label: t("filters.range.this_month"), from: () => firstOfMonth(0), to: () => lastOfMonth(0) },
      { key: "last_month", label: t("filters.range.last_month"), from: () => firstOfMonth(-1), to: () => lastOfMonth(-1) },
    ],
    [t],
  );

  function presetLabel(ctx: RangeCtx): string {
    for (const p of presets) {
      if (ctx.from === p.from() && ctx.to === p.to()) return p.label;
    }
    return `${localizedDate(ctx.from, i18n.language)} ${t("common.range_separator")} ${localizedDate(ctx.to, i18n.language)}`;
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function applyPreset(p: Preset) {
    setCtx({ from: p.from(), to: p.to() });
    setOpen(false);
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: active ? "var(--accent)" : "var(--bg-surface)",
          color: active ? "#fff" : "var(--text-primary)",
          border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
          borderRadius: 8,
          padding: "8px 16px",
          fontSize: 14,
          fontWeight: 600,
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
          boxShadow: active ? "0 1px 3px rgba(91,108,173,0.30)" : "none",
          transition: "all var(--transition)",
        }}
      >
        <span aria-hidden style={{ fontSize: 16 }}>📅</span>
        {presetLabel(ctx)}
        <span style={{ opacity: 0.7 }}>▾</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 50,
            minWidth: 280,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.10)",
            padding: 8,
            color: "var(--text-primary)",
          }}
        >
          {presets.map((p) => {
            const selected = ctx.from === p.from() && ctx.to === p.to();
            return (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p)}
                aria-pressed={selected}
                style={{
                  appearance: "none",
                  border: "none",
                  font: "inherit",
                  textAlign: "left",
                  display: "block",
                  width: "100%",
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderRadius: 4,
                  fontSize: 13,
                  background: selected ? "var(--accent-soft)" : "transparent",
                  color: selected ? "var(--accent)" : "var(--text-primary)",
                  fontWeight: selected ? 500 : 400,
                }}
              >
                {p.label}
              </button>
            );
          })}
          <div style={{ borderTop: "1px solid var(--border-soft)", margin: "6px 0" }} />
          <div style={{ padding: "0 8px", fontSize: 12, color: "var(--text-secondary)", marginBottom: 6 }}>
            {t("filters.range.custom")}
          </div>
          <div style={{ display: "flex", gap: 6, padding: "0 8px 8px", alignItems: "center" }}>
            <input
              type="date"
              lang={i18n.language}
              value={ctx.from}
              max={ctx.to}
              onChange={(e) => setCtx({ from: e.target.value })}
              style={{ flex: 1, fontSize: 13, padding: "4px 6px" }}
            />
            <span style={{ color: "var(--text-tertiary)" }}>{t("common.range_separator")}</span>
            <input
              type="date"
              lang={i18n.language}
              value={ctx.to}
              min={ctx.from}
              onChange={(e) => setCtx({ to: e.target.value })}
              style={{ flex: 1, fontSize: 13, padding: "4px 6px" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
