import { useState, useMemo, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import type { FilterCtx } from "../api/types";
import type { DowFilter, TimeBand } from "../api/rangeContext";
import { DEFAULT_RANGE_DAYS, isoDaysAgo, todayISO } from "../api/rangeContext";
import { RoutesPicker } from "./RoutesPicker";

// ─── types ────────────────────────────────────────────────────────────────────

type Props = {
  value: FilterCtx;
  onChange: (next: FilterCtx) => void;
  /** Disabled while a mutation is in-flight (e.g. updating the thread's filter_ctx server-side) */
  pending?: boolean;
};

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Shared summary helper — mirrors ThreadSidebar's filterSummary logic. */
function filterSummary(
  fc: FilterCtx,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [];

  // Date range
  if (fc.from_date && fc.to_date) {
    const from = new Date(fc.from_date);
    const to = new Date(fc.to_date);
    const days = Math.round((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000));
    if (days === 6 || days === 7) parts.push(t("filters.range.last_7d"));
    else if (days >= 28 && days <= 31) parts.push(t("filters.range.last_30d"));
    else if (days >= 85 && days <= 92) parts.push(t("filters.range.last_90d"));
    else parts.push(`${fc.from_date} 〜 ${fc.to_date}`);
  } else {
    // No explicit range — treat as "last 30 days" default
    parts.push(t("filters.range.last_30d"));
  }

  // Day-of-week
  if (fc.dow && fc.dow !== "all") {
    const dowKey = fc.dow === "weekday" ? "ask.filter_bar.dow_weekday" : "ask.filter_bar.dow_weekend";
    parts.push(t(dowKey));
  }

  // Time band
  if (fc.time_band && fc.time_band !== "all") {
    const tbKey = `filters.time_band.${fc.time_band}`;
    const label = t(tbKey);
    if (label !== tbKey) parts.push(label);
  }

  return parts.join(" ▸ ");
}

function routesSummary(
  fc: FilterCtx,
  t: (key: string) => string,
): string {
  if (!fc.routes || fc.routes.length === 0) {
    return t("ask.filter_bar.no_routes_selected");
  }
  return fc.routes.join(", ");
}

// ─── style helpers ────────────────────────────────────────────────────────────

const pillRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
  padding: "6px 10px",
  background: "var(--bg-surface)",
  border: "1px solid var(--border-soft)",
  borderRadius: "var(--radius)",
  fontSize: 12,
  color: "var(--text-secondary)",
};

const groupLabel: CSSProperties = {
  fontSize: 11,
  color: "var(--text-tertiary)",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  marginBottom: 6,
  display: "block",
};

const pill = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "var(--bg-surface)",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-soft)"}`,
  borderRadius: 999,
  padding: "4px 12px",
  fontSize: 12,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
  transition: "all var(--transition)",
});

const editButtonStyle: CSSProperties = {
  background: "transparent",
  border: "1px solid var(--border-soft)",
  borderRadius: 6,
  padding: "3px 10px",
  fontSize: 12,
  color: "var(--accent)",
  cursor: "pointer",
  fontWeight: 500,
  transition: "opacity var(--transition)",
  marginLeft: "auto",
};

const dateInputStyle: CSSProperties = {
  padding: "4px 8px",
  border: "1px solid var(--border-soft)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--text-primary)",
  background: "var(--bg-surface)",
};

// ─── component ────────────────────────────────────────────────────────────────

export function FilterContextBar({ value, onChange, pending }: Props) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);

  // Draft uses explicit date defaults when value has no dates
  const defaultFrom = isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
  const defaultTo = todayISO();

  const [draft, setDraft] = useState<FilterCtx>(() => ({
    ...value,
    from_date: value.from_date ?? defaultFrom,
    to_date: value.to_date ?? defaultTo,
  }));

  const dowOptions = useMemo<{ value: DowFilter; label: string }[]>(
    () => [
      { value: "all", label: t("ask.filter_bar.dow_all") },
      { value: "weekday", label: t("ask.filter_bar.dow_weekday") },
      { value: "weekend", label: t("ask.filter_bar.dow_weekend") },
    ],
    [t],
  );

  const timeBandOptions = useMemo<{ value: TimeBand; label: string }[]>(
    () => [
      { value: "all", label: t("filters.time_band.all") },
      { value: "morning", label: t("filters.time_band.morning") },
      { value: "forenoon", label: t("filters.time_band.forenoon") },
      { value: "noon", label: t("filters.time_band.noon") },
      { value: "afternoon", label: t("filters.time_band.afternoon") },
      { value: "evening", label: t("filters.time_band.evening") },
      { value: "night", label: t("filters.time_band.night") },
      { value: "late_night", label: t("filters.time_band.late_night") },
    ],
    [t],
  );

  function handleEdit() {
    // Sync draft to current value when opening editor
    setDraft({
      ...value,
      from_date: value.from_date ?? defaultFrom,
      to_date: value.to_date ?? defaultTo,
    });
    setEditing(true);
  }

  function handleApply() {
    onChange(draft);
    setEditing(false);
  }

  function handleCancel() {
    setDraft({
      ...value,
      from_date: value.from_date ?? defaultFrom,
      to_date: value.to_date ?? defaultTo,
    });
    setEditing(false);
  }

  const summary = filterSummary(value, t);
  const routes = routesSummary(value, t);

  // ── collapsed pill row ────────────────────────────────────────────────────
  if (!editing) {
    return (
      <div style={pillRowStyle}>
        <span aria-hidden style={{ fontSize: 14 }}>📅</span>
        <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{summary}</span>
        <span style={{ color: "var(--text-tertiary)" }}>・</span>{/* i18n-ignore: locale-neutral separator */}
        <span>{routes}</span>
        <button
          type="button"
          style={editButtonStyle}
          onClick={handleEdit}
          disabled={pending}
          aria-label={t("ask.filter_bar.edit")}
        >
          {t("ask.filter_bar.edit")}
        </button>
      </div>
    );
  }

  // ── inline editor ─────────────────────────────────────────────────────────
  return (
    <div
      style={{
        padding: 16,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--radius)",
      }}
    >
      {/* Date range */}
      <div style={{ marginBottom: 14 }}>
        <span style={groupLabel}>{t("ask.filter_bar.label_date_range")}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <input
            type="date"
            value={draft.from_date ?? defaultFrom}
            max={draft.to_date ?? defaultTo}
            onChange={(e) => setDraft((d) => ({ ...d, from_date: e.target.value }))}
            disabled={pending}
            style={dateInputStyle}
          />
          <span style={{ color: "var(--text-tertiary)", fontSize: 12 }}>〜</span>
          <input
            type="date"
            value={draft.to_date ?? defaultTo}
            min={draft.from_date ?? defaultFrom}
            onChange={(e) => setDraft((d) => ({ ...d, to_date: e.target.value }))}
            disabled={pending}
            style={dateInputStyle}
          />
        </div>
      </div>

      {/* DOW */}
      <div style={{ marginBottom: 14 }}>
        <span style={groupLabel}>{t("ask.filter_bar.label_dow")}</span>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {dowOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setDraft((d) => ({ ...d, dow: o.value }))}
              disabled={pending}
              style={pill((draft.dow ?? "all") === o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Time band */}
      <div style={{ marginBottom: 14 }}>
        <span style={groupLabel}>{t("ask.filter_bar.label_time_band")}</span>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {timeBandOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setDraft((d) => ({ ...d, time_band: o.value }))}
              disabled={pending}
              style={pill((draft.time_band ?? "all") === o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Routes */}
      <div style={{ marginBottom: 14 }}>
        <span style={groupLabel}>{t("ask.filter_bar.label_routes")}</span>
        <RoutesPicker
          selected={draft.routes ?? []}
          onChange={(routes) => setDraft((d) => ({ ...d, routes }))}
        />
      </div>

      {/* Actions */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          paddingTop: 12,
          borderTop: "1px solid var(--border-soft)",
        }}
      >
        <button
          type="button"
          onClick={handleCancel}
          disabled={pending}
          style={{
            background: "transparent",
            border: "1px solid var(--border-soft)",
            borderRadius: 4,
            padding: "6px 14px",
            fontSize: 13,
            color: "var(--text-secondary)",
            cursor: pending ? "not-allowed" : "pointer",
          }}
        >
          {t("ask.filter_bar.cancel")}
        </button>
        <button
          type="button"
          onClick={handleApply}
          disabled={pending}
          style={{
            background: pending ? "var(--bg-soft)" : "var(--accent)",
            color: pending ? "var(--text-tertiary)" : "#fff",
            border: "none",
            borderRadius: 4,
            padding: "6px 18px",
            fontSize: 13,
            fontWeight: 500,
            cursor: pending ? "not-allowed" : "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            boxShadow: pending ? "none" : "0 1px 2px rgba(91,108,173,0.25)",
          }}
        >
          {pending && (
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: 12,
                height: 12,
                border: "2px solid currentColor",
                borderTopColor: "transparent",
                borderRadius: "50%",
                animation: "fcb-spin 0.7s linear infinite",
              }}
            />
          )}
          {t("ask.filter_bar.apply")}
        </button>
      </div>

      {/* Spinner keyframes (scoped) */}
      <style>{`@keyframes fcb-spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
