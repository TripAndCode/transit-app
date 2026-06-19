import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRoutes, useForecastHeatmap } from "../api/hooks";
import { RoutePickerPill } from "../components/paramPills/RoutePickerPill";
import { OverviewModal } from "../components/OverviewModal";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";
import type { ForecastHeatmapCell } from "../api/types";

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const RAMP_STOPS = 6;

type Tip = { x: number; y: number; text: string } | null;
type View = "hm" | "dow" | "hr" | null;

/** Clickable-card props matching the Overview card pattern (role=button + keyboard). */
function clickable(onClick: () => void) {
  return {
    role: "button",
    tabIndex: 0,
    onClick,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onClick();
      }
    },
  };
}

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  const x = Math.min(tip.x + 14, window.innerWidth - 170);
  const y = Math.min(tip.y + 14, window.innerHeight - 36);
  return (
    <div
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: 90,
        pointerEvents: "none",
        background: "var(--text-primary)",
        color: "#fff",
        fontSize: 12,
        padding: "5px 9px",
        borderRadius: 6,
        boxShadow: "0 4px 14px rgba(0,0,0,.18)",
        whiteSpace: "nowrap",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {tip.text}
    </div>
  );
}

function HeatmapGrid({
  cells,
  big,
  axisMin,
  dayLabel,
  ariaLabel,
  onTip,
  onLeave,
}: {
  cells: ForecastHeatmapCell[];
  big: boolean;
  axisMin: string;
  dayLabel: (dow: number) => string;
  ariaLabel?: string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));
  const labelW = big ? 30 : 22;
  const cellH = big ? 26 : 13;
  const gap = big ? 3 : 2;
  const cols = `${labelW}px repeat(24, 1fr)`;

  return (
    <div role={ariaLabel ? "img" : undefined} aria-label={ariaLabel} onMouseLeave={() => { setHover(null); onLeave(); }}>
      <div style={{ display: "grid", gridTemplateColumns: cols, gap, alignItems: "center" }}>
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return [
            <div key={`l${dow}`} style={{ fontSize: big ? 11 : 10, color: "var(--text-secondary)", textAlign: "right", paddingRight: 5 }}>
              {dayLabel(dow)}
            </div>,
            ...Array.from({ length: 24 }, (_, h) => {
              const c = byKey.get(`${dow}-${h}`);
              const v = c?.expected_avg_min ?? null;
              const key = `${dow}-${h}`;
              if (v == null || !c) {
                return (
                  <div
                    key={key}
                    onMouseEnter={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    onMouseMove={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    style={{ height: cellH, borderRadius: 2, background: "repeating-linear-gradient(45deg,#f0eee9,#f0eee9 3px,#f6f4ef 3px,#f6f4ef 6px)" }}
                  />
                );
              }
              const active = hover === key;
              const text = `${dayLabel(dow)} ${h}:00 · ${v.toFixed(1)}${axisMin}`;
              return (
                <div
                  key={key}
                  data-testid="hm-cell"
                  onMouseEnter={(e) => { setHover(key); onTip(e, text); }}
                  onMouseMove={(e) => onTip(e, text)}
                  style={{
                    height: cellH,
                    borderRadius: 2,
                    background: delayColor(v),
                    opacity: c.low_confidence ? 0.5 : 1,
                    outline: active ? "2px solid var(--accent)" : "none",
                    outlineOffset: 1,
                    boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "none",
                  }}
                >
                  {c.low_confidence && <span data-testid="hm-cell-lowconf" hidden />}
                </div>
              );
            }),
          ];
        })}
      </div>
      {big && (
        <div style={{ display: "grid", gridTemplateColumns: cols, gap, marginTop: 5 }}>
          <span />
          {Array.from({ length: 24 }, (_, h) => (
            <span key={h} style={{ fontSize: 10, color: "var(--text-tertiary)", textAlign: "center" }}>
              {h % 6 === 0 ? h : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function MarginBars({
  values,
  labels,
  testid,
  big,
  sparse,
  axisMin,
  onTip,
  onLeave,
}: {
  values: (number | null)[];
  labels: string[];
  testid: string;
  big: boolean;
  sparse: boolean;
  axisMin: string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const max = Math.max(...values.filter((v): v is number => v != null), 1);
  return (
    <div onMouseLeave={onLeave}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: big ? 150 : 64, borderBottom: "1px solid var(--border-soft)" }}>
        {values.map((v, i) =>
          v == null ? (
            <span key={i} style={{ flex: 1 }} />
          ) : (
            <i
              key={i}
              data-testid={testid}
              onMouseEnter={(e) => onTip(e, `${labels[i]} · ${v.toFixed(1)}${axisMin}`)}
              onMouseMove={(e) => onTip(e, `${labels[i]} · ${v.toFixed(1)}${axisMin}`)}
              style={{ flex: 1, display: "block", height: `${Math.max((v / max) * 100, 1)}%`, background: delayColor(v), borderRadius: "3px 3px 0 0" }}
            />
          ),
        )}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
        {labels.map((l, i) => (
          <span key={i} style={{ flex: 1, textAlign: "center", fontSize: 10, color: "var(--text-tertiary)" }}>
            {sparse ? (i % 6 === 0 ? i : "") : l}
          </span>
        ))}
      </div>
    </div>
  );
}

function StatStrip({ stats }: { stats: { label: string; value: string }[] }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
      {stats.map((s) => (
        <div key={s.label} style={{ flex: 1, minWidth: 110, background: "var(--bg-soft)", borderRadius: 8, padding: "9px 12px" }}>
          <b style={{ display: "block", fontSize: 16, fontVariantNumeric: "tabular-nums" }}>{s.value}</b>
          <small style={{ fontSize: 10, color: "var(--text-2, var(--text-secondary))" }}>{s.label}</small>
        </div>
      ))}
    </div>
  );
}

function Card({ title, sublabel, expand, testid, onOpen, children }: {
  title: string;
  sublabel: string;
  expand: string;
  testid: string;
  onOpen: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="ov-card ov-clickable" data-testid={testid} aria-label={title} {...clickable(onOpen)}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
        <span aria-hidden style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{expand} ⤢</span>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "0 0 10px" }}>{sublabel}</p>
      {children}
    </div>
  );
}

export function ForecastTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const aid = agencyId ? Number(agencyId) : null;

  const { data: routes } = useRoutes(aid);
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const firstRoute = (routes ?? []).find((r) => r.route_code)?.route_code ?? null;
  const route = selectedRoute ?? firstRoute ?? "";

  const { data, isPending, error, refetch } = useForecastHeatmap(aid, route);
  const cells = data?.cells ?? [];

  const [tip, setTip] = useState<Tip>(null);
  const [view, setView] = useState<View>(null);

  const populated = cells.filter((c) => c.expected_avg_min != null);
  const max = Math.max(...populated.map((c) => c.expected_avg_min as number), 1);
  const min = populated.length ? Math.min(...populated.map((c) => c.expected_avg_min as number)) : 0;
  const totalN = populated.reduce((a, c) => a + c.samples, 0);
  const mean = totalN ? populated.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / totalN : 0;
  const allNull = data != null && populated.length === 0;
  const peak = populated.reduce<ForecastHeatmapCell | null>((b, c) => (!b || (c.expected_avg_min as number) > (b.expected_avg_min as number) ? c : b), null);
  const calm = populated.reduce<ForecastHeatmapCell | null>((b, c) => (!b || (c.expected_avg_min as number) < (b.expected_avg_min as number) ? c : b), null);

  const margin = (pick: (c: ForecastHeatmapCell) => number, n: number): (number | null)[] =>
    Array.from({ length: n }, (_, i) => {
      const cs = cells.filter((c) => pick(c) === i && c.expected_avg_min != null);
      const s = cs.reduce((a, c) => a + c.samples, 0);
      return s ? cs.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / s : null;
    });
  const dowAvg = margin((c) => c.dow - 1, 7);
  const hourAvg = margin((c) => c.hour, 24);

  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const min1 = t("forecast.axis_min");
  const onTip = (e: React.MouseEvent, text: string) => setTip({ x: e.clientX, y: e.clientY, text });
  const onLeave = () => setTip(null);

  // worst/calmest index of a margin
  const argExtreme = (vals: (number | null)[], worst: boolean) => {
    let idx = -1;
    let best = worst ? -Infinity : Infinity;
    vals.forEach((v, i) => {
      if (v != null && (worst ? v > best : v < best)) {
        best = v;
        idx = i;
      }
    });
    return idx;
  };

  const dowLabels = Array.from({ length: 7 }, (_, i) => dayLabel(i + 1));
  const hourLabels = Array.from({ length: 24 }, (_, h) => `${h}:00`);

  const modalTitle = view === "hm" ? t("forecast.heatmap_title") : view === "dow" ? t("forecast.dow_summary") : t("forecast.hour_summary");

  return (
    <div style={{ padding: 24, maxWidth: 880, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>{t("forecast.eyebrow")}</div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>{t("forecast.title")}</h1>

      {aid != null && (
        <div style={{ display: "flex", gap: 10, marginBottom: 22, alignItems: "center", flexWrap: "wrap" }}>
          <RoutePickerPill label={t("forecast.route_label")} value={route || null} agencyId={aid} placeholder={t("forecast.route_placeholder")} onChange={setSelectedRoute} />
        </div>
      )}

      {!route && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.pick_prompt")}</p>}
      {route && isPending && <Skeleton height={200} />}
      {route && error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {route && data && allNull && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>}

      {route && data && !allNull && (
        <>
          <Card title={t("forecast.heatmap_title")} sublabel={t("forecast.heatmap_caption")} expand={t("forecast.expand")} testid="fc-card-hm" onOpen={() => setView("hm")}>
            <HeatmapGrid cells={cells} big={false} axisMin={min1} dayLabel={dayLabel} onTip={onTip} onLeave={onLeave} />
          </Card>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
            <Card title={t("forecast.dow_summary")} sublabel={t("forecast.click_hint")} expand={t("forecast.expand")} testid="fc-card-dow" onOpen={() => setView("dow")}>
              <MarginBars values={dowAvg} labels={dowLabels} testid="dow-bar" big={false} sparse={false} axisMin={min1} onTip={onTip} onLeave={onLeave} />
            </Card>
            <Card title={t("forecast.hour_summary")} sublabel={t("forecast.click_hint")} expand={t("forecast.expand")} testid="fc-card-hr" onOpen={() => setView("hr")}>
              <MarginBars values={hourAvg} labels={hourLabels} testid="hr-bar" big={false} sparse axisMin={min1} onTip={onTip} onLeave={onLeave} />
            </Card>
          </div>
        </>
      )}

      {view && data && (
        <OverviewModal isOpen onClose={() => setView(null)} title={modalTitle}>
          {view === "hm" && peak && calm && (
            <>
              <StatStrip
                stats={[
                  { label: t("forecast.stat_worst"), value: `${dayLabel(peak.dow)} ${peak.hour}:00 · ${(peak.expected_avg_min as number).toFixed(1)}${min1}` },
                  { label: t("forecast.stat_calmest"), value: `${dayLabel(calm.dow)} ${calm.hour}:00 · ${(calm.expected_avg_min as number).toFixed(1)}${min1}` },
                  { label: t("forecast.stat_mean"), value: `${mean.toFixed(1)}${min1}` },
                  { label: t("forecast.stat_samples"), value: totalN.toLocaleString() },
                ]}
              />
              <HeatmapGrid cells={cells} big axisMin={min1} dayLabel={dayLabel} ariaLabel={t("forecast.heatmap_aria")} onTip={onTip} onLeave={onLeave} />
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 13, fontSize: 11, color: "var(--text-secondary)" }}>
                <span style={{ fontVariantNumeric: "tabular-nums" }}>{min.toFixed(1)}</span>
                <span style={{ display: "inline-flex", gap: 2 }}>
                  {Array.from({ length: RAMP_STOPS }, (_, i) => (
                    <span key={i} style={{ width: 14, height: 14, borderRadius: 2, background: delayColor(min + ((max - min) * i) / (RAMP_STOPS - 1)) }} />
                  ))}
                </span>
                <span style={{ fontVariantNumeric: "tabular-nums" }}>{max.toFixed(1)}</span>
                <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{t("forecast.legend_unit")}</span>
              </div>
            </>
          )}
          {(view === "dow" || view === "hr") && (() => {
            const vals = view === "dow" ? dowAvg : hourAvg;
            const labels = view === "dow" ? dowLabels : hourLabels;
            const wi = argExtreme(vals, true);
            const ci = argExtreme(vals, false);
            // Route avg + sample count are properties of the route, not the
            // collapsed axis: reuse the sample-weighted `mean`/`totalN` so all
            // three modals show the same figures (a per-axis unweighted mean
            // would disagree with the heatmap modal).
            return (
              <>
                <StatStrip
                  stats={[
                    { label: t("forecast.stat_worst"), value: wi >= 0 ? `${labels[wi]} · ${(vals[wi] as number).toFixed(1)}${min1}` : "—" },
                    { label: t("forecast.stat_calmest"), value: ci >= 0 ? `${labels[ci]} · ${(vals[ci] as number).toFixed(1)}${min1}` : "—" },
                    { label: t("forecast.stat_mean"), value: `${mean.toFixed(1)}${min1}` },
                    { label: t("forecast.stat_samples"), value: totalN.toLocaleString() },
                  ]}
                />
                <MarginBars values={vals} labels={labels} testid={view === "dow" ? "dow-bar-big" : "hr-bar-big"} big sparse={view === "hr"} axisMin={min1} onTip={onTip} onLeave={onLeave} />
              </>
            );
          })()}
          {data.disclaimer && (
            <p style={{ color: "var(--text-tertiary)", fontSize: 11, lineHeight: 1.5, marginTop: 16, borderTop: "1px solid var(--border-soft)", paddingTop: 12 }}>
              {data.disclaimer}
            </p>
          )}
        </OverviewModal>
      )}

      <Tooltip tip={tip} />
    </div>
  );
}
