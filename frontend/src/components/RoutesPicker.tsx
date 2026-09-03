import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRoutes } from "../api/hooks";
import { routeDisplayName } from "../api/routeDisplayName";
import type { Route } from "../api/types";

type RouteVariant = { code: string; long_name: string | null; headsigns: string[] };
type RouteGroup = {
  name: string;
  variants: RouteVariant[];
  shared_long_name: string | null;
};

function useAgencyId(): number | null {
  const { agencyId } = useParams();
  return agencyId ? Number(agencyId) : null;
}

function variantLabel(v: RouteVariant): string {
  const long = v.long_name?.trim();
  const heads = v.headsigns.filter((h) => h.trim() !== "");
  const headsuffix = heads.length > 0 ? ` (${heads.join(" / ")})` : "";
  if (long) return long + headsuffix;
  if (heads.length > 0) return heads.join(" / ");
  return v.code;
}

// Module-scope pure function rather than an in-render IIFE: an ordinary
// CallExpression with an Identifier callee is unambiguously memoized by the
// React Compiler the same way as any other function call in these
// components, closing the "does the compiler memoize an inline IIFE the
// same way" question this ban's residual-risk note used to leave open.
function buildRouteGroups(data: Route[] | undefined): RouteGroup[] {
  if (!data) return [];
  const m = new Map<string, RouteVariant[]>();
  for (const r of data) {
    if (!r.route_code) continue;
    const name = routeDisplayName(r) || r.route_code;
    const arr = m.get(name) || [];
    if (!arr.some((v) => v.code === r.route_code)) {
      arr.push({
        code: r.route_code,
        long_name: r.route_long_name,
        headsigns: r.trip_headsigns ?? [],
      });
    }
    m.set(name, arr);
  }
  return Array.from(m, ([name, variants]) => {
    variants.sort((a, b) => a.code.localeCompare(b.code));
    const longs = new Set(variants.map((v) => v.long_name?.trim() || ""));
    const shared = longs.size === 1 ? variants[0].long_name : null;
    return { name, variants, shared_long_name: shared };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

export function RoutesPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { data, isPending, refetch } = useRoutes(id);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // Render cap keeps the first paint cheap for huge agencies; "show more"
  // raises it instead of silently dropping the tail (search still scans all).
  const [visibleCap, setVisibleCap] = useState(200);

  const groups: RouteGroup[] = buildRouteGroups(data);

  const filterQuery = filter.trim().toLowerCase();
  const filteredGroups = filterQuery
    ? groups
        .map((g) => {
          const variants = g.variants.filter((v) => {
            const blob = (
              g.name +
              " " +
              (v.long_name || "") +
              " " +
              v.headsigns.join(" ") +
              " " +
              v.code
            ).toLowerCase();
            return blob.includes(filterQuery);
          });
          return { ...g, variants };
        })
        .filter((g) => g.variants.length > 0 || g.name.toLowerCase().includes(filterQuery))
    : groups;

  function toggleCode(code: string) {
    onChange(selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code]);
  }

  function toggleGroup(g: RouteGroup) {
    const codes = g.variants.map((v) => v.code);
    const sel = new Set(selected);
    const allOn = codes.every((c) => sel.has(c));
    if (allOn) {
      onChange(selected.filter((c) => !codes.includes(c)));
    } else {
      const next = new Set(selected);
      for (const c of codes) next.add(c);
      onChange(Array.from(next));
    }
  }

  function toggleExpanded(name: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div>
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder={t("filters.routes.search_placeholder")}
        style={{ width: "100%", marginBottom: 6, fontSize: 13 }}
      />
      <div
        style={{
          maxHeight: 220,
          overflowY: "auto",
          border: "1px solid var(--border-soft)",
          borderRadius: 4,
        }}
      >
        {filteredGroups.slice(0, visibleCap).map((g) => {
          const codes = g.variants.map((v) => v.code);
          const allOn = codes.every((c) => selected.includes(c));
          const someOn = !allOn && codes.some((c) => selected.includes(c));
          const isOpen = expanded.has(g.name);
          const multi = g.variants.length > 1;
          const topSuffix = !multi
            ? g.variants[0].long_name?.trim() || ""
            : g.shared_long_name?.trim() || "";
          return (
            <div key={g.name} style={{ borderBottom: "1px solid var(--border-soft)" }}>
              <div
                style={{
                  padding: "6px 10px",
                  background: allOn
                    ? "var(--accent-soft)"
                    : someOn
                      ? "rgba(91,108,173,0.06)"
                      : "transparent",
                  fontSize: 13,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <input
                  type="checkbox"
                  checked={allOn}
                  ref={(el) => {
                    if (el) el.indeterminate = someOn;
                  }}
                  onChange={() => toggleGroup(g)}
                />
                <button
                  type="button"
                  onClick={() => toggleGroup(g)}
                  style={{
                    appearance: "none",
                    background: "transparent",
                    border: "none",
                    font: "inherit",
                    color: "inherit",
                    textAlign: "left",
                    flex: 1,
                    cursor: "pointer",
                    padding: 0,
                  }}
                >
                  <span style={{ fontWeight: 600 }}>{g.name}</span>
                  {topSuffix && (
                    <span style={{ color: "var(--text-secondary)" }}> {topSuffix}</span>
                  )}
                  {multi && (
                    <span style={{ color: "var(--text-tertiary)" }}> {t("filters.routes.variant_count", { count: g.variants.length })}</span>
                  )}
                </button>
                {multi && (
                  <button
                    type="button"
                    onClick={() => toggleExpanded(g.name)}
                    aria-label={isOpen ? t("common.close") : t("common.expand")}
                    style={{
                      background: "transparent",
                      border: "none",
                      padding: "2px 6px",
                      cursor: "pointer",
                      color: "var(--text-tertiary)",
                      fontSize: 12,
                    }}
                  >
                    {isOpen ? "▾" : "▸"}
                  </button>
                )}
              </div>
              {multi && isOpen && (
                <div style={{ paddingLeft: 26, background: "var(--bg-soft)" }}>
                  {g.variants.map((v) => {
                    const on = selected.includes(v.code);
                    return (
                      <label
                        key={v.code}
                        style={{
                          padding: "5px 10px",
                          cursor: "pointer",
                          background: on ? "var(--accent-soft)" : "transparent",
                          fontSize: 12,
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          borderTop: "1px dashed var(--border-soft)",
                        }}
                      >
                        <input type="checkbox" checked={on} onChange={() => toggleCode(v.code)} />
                        <span style={{ color: "var(--text-secondary)", flex: 1 }}>{variantLabel(v)}</span>
                        <span
                          style={{
                            color: "var(--text-tertiary)",
                            fontFamily: "ui-monospace, monospace",
                            fontSize: 11,
                          }}
                        >
                          {v.code}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
        {filteredGroups.length > visibleCap && (
          <button
            type="button"
            onClick={() => setVisibleCap((c) => c + 200)}
            style={{
              width: "100%",
              background: "transparent",
              border: "none",
              color: "var(--accent)",
              padding: "8px 10px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t("filters.routes.show_more", { count: filteredGroups.length - visibleCap })}
          </button>
        )}
        {filteredGroups.length === 0 && isPending && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("common.loading")}
          </div>
        )}
        {filteredGroups.length === 0 && !isPending && filter.trim() !== "" && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>{t("common.no_match")}</div>
        )}
        {filteredGroups.length === 0 && !isPending && filter.trim() === "" && (
          <div style={{ padding: 10, color: "var(--text-tertiary)", fontSize: 13 }}>
            {t("filters.routes.empty_title")}
            {" "}
            {t("filters.routes.empty_hint")}
            <button
              type="button"
              onClick={() => refetch()}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--accent)",
                padding: "2px 6px",
                fontSize: 12,
                cursor: "pointer",
                textDecoration: "underline",
                marginLeft: 6,
              }}
            >
              {t("common.reload")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
