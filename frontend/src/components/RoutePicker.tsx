import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRoutes } from "../api/hooks";

type Props = {
  agencyId: number;
  value: string | null;
  onChange: (route_id: string | null) => void;
  placeholder?: string;
};

/**
 * Single-route picker — wraps the same route data as RoutesPicker but
 * only allows one selection at a time.  Selecting a route that is already
 * selected deselects it (clears to null).
 */
export function RoutePicker({ agencyId, value, onChange, placeholder }: Props) {
  const { t } = useTranslation();
  const { data, isPending } = useRoutes(agencyId);
  const [filter, setFilter] = useState("");

  type RouteOption = { code: string; label: string };

  const options = useMemo<RouteOption[]>(() => {
    if (!data) return [];
    const seen = new Set<string>();
    const result: RouteOption[] = [];
    for (const r of data) {
      if (!r.route_code || seen.has(r.route_code)) continue;
      seen.add(r.route_code);
      const name = r.route_short_name || r.route_long_name || r.route_id || r.route_code;
      const long = r.route_long_name?.trim();
      const label = long && long !== name ? `${name} ${long}` : name;
      result.push({ code: r.route_code, label });
    }
    return result.sort((a, b) => a.label.localeCompare(b.label));
  }, [data]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) =>
      (o.label + " " + o.code).toLowerCase().includes(q),
    );
  }, [options, filter]);

  const selectedLabel = value
    ? (options.find((o) => o.code === value)?.label ?? value)
    : null;

  return (
    <div>
      {/* Show current selection with a clear button */}
      {value && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            marginBottom: 4,
            padding: "4px 8px",
            background: "var(--accent-soft)",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          <span style={{ flex: 1 }}>{selectedLabel}</span>
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={t("common.close")}
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--text-tertiary)",
              padding: "0 2px",
              fontSize: 14,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </div>
      )}

      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder={placeholder ?? t("filters.routes.search_placeholder")}
        style={{ width: "100%", marginBottom: 4, fontSize: 13 }}
      />

      <div
        style={{
          maxHeight: 160,
          overflowY: "auto",
          border: "1px solid var(--border-soft)",
          borderRadius: 4,
        }}
      >
        {filtered.slice(0, 200).map((opt) => {
          const selected = opt.code === value;
          return (
            <div
              key={opt.code}
              onClick={() => onChange(selected ? null : opt.code)}
              style={{
                padding: "5px 10px",
                cursor: "pointer",
                background: selected ? "var(--accent-soft)" : "transparent",
                fontSize: 13,
                display: "flex",
                alignItems: "center",
                gap: 6,
                borderBottom: "1px solid var(--border-soft)",
              }}
            >
              <input type="radio" readOnly checked={selected} style={{ flexShrink: 0 }} />
              <span style={{ flex: 1 }}>{opt.label}</span>
              <span
                style={{
                  color: "var(--text-tertiary)",
                  fontFamily: "ui-monospace, monospace",
                  fontSize: 11,
                }}
              >
                {opt.code}
              </span>
            </div>
          );
        })}
        {filtered.length === 0 && isPending && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("common.loading")}
          </div>
        )}
        {filtered.length === 0 && !isPending && filter.trim() !== "" && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("common.no_match")}
          </div>
        )}
        {filtered.length === 0 && !isPending && filter.trim() === "" && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("filters.routes.empty_title")}
          </div>
        )}
      </div>
    </div>
  );
}
