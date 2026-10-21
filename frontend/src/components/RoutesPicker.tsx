import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useRoutes } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { distinctRouteTexts, routeDisplayName, routeNameText, type RouteLabelParts } from "../api/routeDisplayName";
import { routeWhereText, useRouteNames } from "../api/useRouteNames";
import type { Route } from "../api/types";
import { FILTER_SEPARATOR } from "../utils/format";
import { RoutePartsLabel } from "./RouteLabel";
import { searchKey } from "../utils/searchText";

type RouteVariant = { code: string; long_name: string | null; headsigns: string[]; searchText: string };
type RouteGroup = {
  name: string;
  variants: RouteVariant[];
  shared_long_name: string | null;
};

/** What sets a variant apart within its line: its own line name, unless the
 *  line's row already shows it, and where it runs, or its headsigns while
 *  the feed names no terminals. */
function variantText(v: RouteVariant, parts: RouteLabelParts | undefined, shownLine: string | null, t: TFunction): string {
  const where = parts ? routeWhereText(parts, t) : null;
  const heads = v.headsigns.filter((h) => h.trim() !== "");
  const line = parts ? parts.line : v.long_name?.trim() || null;
  const detail = where ?? heads.join(" / ");
  return [line === shownLine ? null : line, detail].filter(Boolean).join(FILTER_SEPARATOR) || (parts ? routeNameText(parts) : v.code);
}

/** Each variant's row text, with its route_id added only where two rows of
 *  one line would otherwise read the same. */
function variantTexts(
  variants: RouteVariant[],
  parts: ReadonlyMap<string, RouteLabelParts>,
  shownLine: string | null,
  t: TFunction,
): Map<string, string> {
  return distinctRouteTexts(variants.map((v) => {
    const text = variantText(v, parts.get(v.code), shownLine, t);
    return { key: v.code, name: text, full: text, id: parts.get(v.code)?.id ?? v.code };
  }));
}

// A module-scope function, not an in-render IIFE: the React Compiler memoizes
// a plain call like this one as it does any other.
function buildRouteGroups(data: Route[] | undefined): RouteGroup[] {
  if (!data) return [];
  const m = new Map<string, RouteVariant[]>();
  for (const r of data) {
    if (!r.route_code) continue;
    const name = routeDisplayName(r) || r.route_code;
    const arr = m.get(name) || [];
    if (!arr.some((v) => v.code === r.route_code)) {
      const headsigns = r.trip_headsigns ?? [];
      arr.push({
        code: r.route_code,
        long_name: r.route_long_name,
        headsigns,
        // Folded here, once per load, not on every keystroke of the filter.
        searchText: searchKey(`${name} ${r.route_long_name || ""} ${headsigns.join(" ")} ${r.route_code}`),
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

type RouteDelay = { avg_min: number; samples: number };

/** The samples-weighted mean delay of a group's variants that have data. */
function groupMean(variants: RouteVariant[], delays: ReadonlyMap<string, RouteDelay> | undefined): number | null {
  if (!delays) return null;
  let samples = 0;
  let weighted = 0;
  for (const v of variants) {
    const d = delays.get(v.code);
    if (!d) continue;
    samples += d.samples;
    weighted += d.avg_min * d.samples;
  }
  return samples > 0 ? weighted / samples : null;
}

export function RoutesPicker({
  selected,
  onChange,
  delays,
  renderDelay,
}: {
  selected: string[];
  onChange: (v: string[]) => void;
  /** Mean delay per route code; each line shows its own beside its name. */
  delays?: ReadonlyMap<string, RouteDelay>;
  renderDelay?: (minutes: number) => ReactNode;
}) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { data, isPending, refetch } = useRoutes(id);
  const names = useRouteNames(id);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // Render cap keeps the first paint cheap for huge agencies; "show more"
  // raises it instead of silently dropping the tail (search still scans all).
  const [visibleCap, setVisibleCap] = useState(200);

  const groups: RouteGroup[] = buildRouteGroups(data);

  const filterQuery = searchKey(filter);
  const filteredGroups = filterQuery
    ? groups
        .map((g) => {
          const variants = g.variants.filter((v) => v.searchText.includes(filterQuery));
          return { ...g, variants };
        })
        .filter((g) => g.variants.length > 0 || searchKey(g.name).includes(filterQuery))
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
        aria-label={t("filters.routes.search_label")}
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
          const mean = renderDelay ? groupMean(g.variants, delays) : null;
          // The line reads by its first variant's label, its line name kept
          // only when every variant shares it. With neither a plate nor a
          // shared line name (a brand short name over differing long names),
          // the label would be empty, so the line reads by its group name.
          const first = names.parts.get(g.variants[0].code);
          const sharedLine = first && g.variants.every((v) => names.parts.get(v.code)?.line === first.line) ? first.line : null;
          const lineParts = first && (first.plate || sharedLine) ? (sharedLine === first.line ? first : { ...first, line: sharedLine }) : null;
          const lineText = lineParts ? routeNameText(lineParts) : g.name;
          const rowTexts = multi && isOpen ? variantTexts(g.variants, names.parts, sharedLine, t) : null;
          return (
            <div key={g.name} style={{ borderBottom: "1px solid var(--border-soft)" }}>
              <div
                style={{
                  padding: "6px 10px",
                  background: allOn
                    ? "var(--accent-soft)"
                    : someOn
                      ? "var(--accent-soft)"
                      : "transparent",
                  fontSize: 13,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  minHeight: 36,
                }}
              >
                <input
                  type="checkbox"
                  checked={allOn}
                  aria-label={lineText}
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
                  {lineParts ? <RoutePartsLabel parts={lineParts} compact nameOnly /> : <span style={{ fontWeight: 600 }}>{g.name}</span>}
                  {multi && (
                    <span style={{ color: "var(--text-tertiary)" }}> {t("filters.routes.variant_count", { count: g.variants.length })}</span>
                  )}
                </button>
                {mean != null && renderDelay?.(mean)}
                {multi && (
                  <button
                    type="button"
                    onClick={() => toggleExpanded(g.name)}
                    aria-label={isOpen ? t("common.close") : t("common.expand")}
                    style={{
                      background: "transparent",
                      border: "none",
                      padding: "2px 6px",
                      minWidth: 32,
                      minHeight: 32,
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
                          minHeight: 32,
                          borderTop: "1px dashed var(--border-soft)",
                        }}
                      >
                        <input type="checkbox" checked={on} onChange={() => toggleCode(v.code)} />
                        <span style={{ color: "var(--text-secondary)", flex: 1 }} title={names.parts.get(v.code)?.id ?? v.code}>
                          {rowTexts?.get(v.code)}
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
