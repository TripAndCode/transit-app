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

type Tip = { x: number; y: number; text: string } | null;
type Slot = { title: string; value: number; samples: number; lowConf: boolean; ctx: string; swatch: string };

/** Cursor-following tooltip (instant, no native-title delay). */
function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  const x = Math.min(tip.x + 14, window.innerWidth - 160);
  const y = Math.min(tip.y + 14, window.innerHeight - 36);
  return (
    <div
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: 50,
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

/** Day-of-week × hour grid. Rows Mon–Sun, cols 0–23. delayColor fill; instant
 *  tooltip + cursor-tracking highlight; readout line (peak default); click→modal. */
function Heatmap({
  cells,
  axisMin,
  ariaLabel,
  peakLabel,
  dayLabel,
  onTip,
  onLeave,
  onSelect,
}: {
  cells: ForecastHeatmapCell[];
  axisMin: string;
  ariaLabel: string;
  peakLabel: string;
  dayLabel: (dow: number) => string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
  onSelect: (c: ForecastHeatmapCell) => void;
}) {
  const [hover, setHover] = useState<ForecastHeatmapCell | null>(null);
  const peak = cells.reduce<ForecastHeatmapCell | null>(
    (best, c) => (c.expected_avg_min != null && (!best || c.expected_avg_min > (best.expected_avg_min ?? -1)) ? c : best),
    null,
  );
  const shown = hover ?? peak;
  const slot = (c: ForecastHeatmapCell) => `${dayLabel(c.dow)} ${c.hour}:00`;
  const byKey = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));

  return (
    <div>
      <div style={{ minHeight: 20, fontSize: 12, color: "var(--text-secondary)", marginBottom: 6, fontVariantNumeric: "tabular-nums" }}>
        {shown && shown.expected_avg_min != null && (
          <>
            {!hover && (
              <span style={{ fontSize: 10, letterSpacing: ".05em", textTransform: "uppercase", color: "var(--text-tertiary)", marginRight: 6 }}>
                {peakLabel}
              </span>
            )}
            <b style={{ color: "var(--text-primary)" }}>{slot(shown)}</b> · {shown.expected_avg_min.toFixed(1)}
            {axisMin}
          </>
        )}
      </div>
      <div
        role="group"
        aria-label={ariaLabel}
        onMouseLeave={() => {
          setHover(null);
          onLeave();
        }}
        style={{ display: "grid", gridTemplateColumns: "26px repeat(24, 1fr)", gap: 2, alignItems: "center" }}
      >
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return [
            <div key={`l${dow}`} style={{ fontSize: 11, color: "var(--text-secondary)", textAlign: "right", paddingRight: 6 }}>
              {dayLabel(dow)}
            </div>,
            ...Array.from({ length: 24 }, (_, h) => {
              const c = byKey.get(`${dow}-${h}`);
              const v = c?.expected_avg_min ?? null;
              const active = hover != null && hover.dow === dow && hover.hour === h;
              if (v == null || !c) {
                return (
                  <div
                    key={`${dow}-${h}`}
                    onMouseEnter={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    onMouseMove={(e) => onTip(e, `${dayLabel(dow)} ${h}:00 · —`)}
                    onMouseLeave={onLeave}
                    style={{
                      height: 20,
                      borderRadius: 2,
                      background:
                        "repeating-linear-gradient(45deg,#f0eee9,#f0eee9 3px,#f6f4ef 3px,#f6f4ef 6px)",
                    }}
                  />
                );
              }
              const text = `${slot(c)} · ${v.toFixed(1)}${axisMin}`;
              return (
                <button
                  key={`${dow}-${h}`}
                  type="button"
                  data-testid="hm-cell"
                  aria-label={text}
                  onMouseEnter={(e) => {
                    setHover(c);
                    onTip(e, text);
                  }}
                  onMouseMove={(e) => onTip(e, text)}
                  onClick={() => onSelect(c)}
                  style={{
                    height: 20,
                    width: "100%",
                    padding: 0,
                    border: "none",
                    display: "block",
                    borderRadius: 2,
                    cursor: "pointer",
                    background: delayColor(v),
                    opacity: c.low_confidence ? 0.5 : 1,
                    outline: active ? "2px solid var(--accent)" : "none",
                    outlineOffset: 1,
                    boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "none",
                  }}
                >
                  {c.low_confidence && <span data-testid="hm-cell-lowconf" hidden />}
                </button>
              );
            }),
          ];
        })}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "26px repeat(24, 1fr)", gap: 2, marginTop: 5 }}>
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} style={{ fontSize: 10, color: "var(--text-tertiary)", textAlign: "center" }}>
            {h % 6 === 0 ? h : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

/** A row of margin bars (the heatmap's row or column averages). */
function MarginBars({
  values,
  labels,
  testid,
  max,
  axisMin,
  sparseLabels,
  onTip,
  onLeave,
  onSelect,
}: {
  values: (number | null)[];
  labels: string[];
  testid: string;
  max: number;
  axisMin: string;
  sparseLabels: boolean;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
  onSelect: (i: number) => void;
}) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 84, borderBottom: "1px solid var(--border-soft)" }}>
        {values.map((v, i) => {
          if (v == null) return <span key={i} style={{ flex: 1 }} />;
          const text = `${labels[i]} · ${v.toFixed(1)}${axisMin}`;
          return (
            <button
              key={i}
              type="button"
              data-testid={testid}
              aria-label={text}
              onMouseEnter={(e) => onTip(e, text)}
              onMouseMove={(e) => onTip(e, text)}
              onMouseLeave={onLeave}
              onClick={() => onSelect(i)}
              style={{
                flex: 1,
                padding: 0,
                border: "none",
                display: "block",
                height: `${Math.max((v / max) * 100, 1)}%`,
                background: delayColor(v),
                borderRadius: "3px 3px 0 0",
                cursor: "pointer",
              }}
            />
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
        {labels.map((l, i) => (
          <span key={i} style={{ flex: 1, textAlign: "center", fontSize: 10, color: "var(--text-tertiary)" }}>
            {sparseLabels ? (i % 6 === 0 ? i : "") : l}
          </span>
        ))}
      </div>
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
  const [modal, setModal] = useState<Slot | null>(null);

  // Render-derived stats (sample-weighted). React Compiler is on — no memo.
  const populated = cells.filter((c) => c.expected_avg_min != null);
  const max = Math.max(...populated.map((c) => c.expected_avg_min as number), 1);
  const min = populated.length ? Math.min(...populated.map((c) => c.expected_avg_min as number)) : 0;
  const totalN = populated.reduce((a, c) => a + c.samples, 0);
  const mean = totalN ? populated.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / totalN : 0;
  const allNull = data != null && populated.length === 0;

  function margin(pick: (c: ForecastHeatmapCell) => number, n: number): (number | null)[] {
    return Array.from({ length: n }, (_, i) => {
      const cs = cells.filter((c) => pick(c) === i && c.expected_avg_min != null);
      const s = cs.reduce((a, c) => a + c.samples, 0);
      return s ? cs.reduce((a, c) => a + (c.expected_avg_min as number) * c.samples, 0) / s : null;
    });
  }
  const dowAvg = margin((c) => c.dow - 1, 7);
  const hourAvg = margin((c) => c.hour, 24);

  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const ctxFor = (v: number) => {
    if (v >= max - 1e-9) return t("forecast.m_peak");
    if (v <= min + 1e-9) return t("forecast.m_calmest");
    const dir = v >= mean ? t("forecast.m_dir_slower") : t("forecast.m_dir_calmer");
    return t("forecast.m_vs_mean", { diff: Math.abs(v - mean).toFixed(1), dir, mean: mean.toFixed(1) });
  };
  const onTip = (e: React.MouseEvent, text: string) => setTip({ x: e.clientX, y: e.clientY, text });
  const onLeave = () => setTip(null);

  // legend swatches sampled across the delay range
  const legendSwatches = Array.from({ length: 6 }, (_, i) => delayColor(min + ((max - min) * i) / 5));

  return (
    <div style={{ padding: 24, maxWidth: 880, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>{t("forecast.eyebrow")}</div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>{t("forecast.title")}</h1>

      {aid != null && (
        <div style={{ display: "flex", gap: 10, marginBottom: 24, alignItems: "center", flexWrap: "wrap" }}>
          <RoutePickerPill
            label={t("forecast.route_label")}
            value={route || null}
            agencyId={aid}
            placeholder={t("forecast.route_placeholder")}
            onChange={setSelectedRoute}
          />
        </div>
      )}

      {!route && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.pick_prompt")}</p>}
      {route && isPending && <Skeleton height={200} />}
      {route && error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {route && data && allNull && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>}

      {route && data && !allNull && (
        <>
          <section style={{ marginBottom: 32 }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 2px" }}>{t("forecast.heatmap_title")}</h2>
            <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "0 0 10px" }}>{t("forecast.heatmap_caption")}</p>
            <Heatmap
              cells={cells}
              axisMin={t("forecast.axis_min")}
              ariaLabel={t("forecast.heatmap_svg_aria")}
              peakLabel={t("forecast.peak")}
              dayLabel={dayLabel}
              onTip={onTip}
              onLeave={onLeave}
              onSelect={(c) =>
                setModal({
                  title: `${dayLabel(c.dow)} ${c.hour}:00`,
                  value: c.expected_avg_min as number,
                  samples: c.samples,
                  lowConf: c.low_confidence,
                  ctx: ctxFor(c.expected_avg_min as number),
                  swatch: delayColor(c.expected_avg_min as number),
                })
              }
            />
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 13, fontSize: 11, color: "var(--text-secondary)" }}>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{min.toFixed(1)}</span>
              <span style={{ display: "inline-flex", gap: 2 }}>
                {legendSwatches.map((c, i) => (
                  <span key={i} style={{ width: 14, height: 14, borderRadius: 2, background: c }} />
                ))}
              </span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{max.toFixed(1)}</span>
              <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{t("forecast.legend_unit")}</span>
            </div>
            <p style={{ color: "var(--text-tertiary)", fontSize: 11, margin: "8px 0 0" }}>{t("forecast.click_hint")}</p>
          </section>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28 }}>
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 8px" }}>{t("forecast.dow_summary")}</h2>
              <MarginBars
                values={dowAvg}
                labels={Array.from({ length: 7 }, (_, i) => dayLabel(i + 1))}
                testid="dow-bar"
                max={max}
                axisMin={t("forecast.axis_min")}
                sparseLabels={false}
                onTip={onTip}
                onLeave={onLeave}
                onSelect={(i) => {
                  const v = dowAvg[i];
                  if (v == null) return;
                  setModal({ title: dayLabel(i + 1), value: v, samples: cells.filter((c) => c.dow === i + 1).reduce((a, c) => a + c.samples, 0), lowConf: false, ctx: ctxFor(v), swatch: delayColor(v) });
                }}
              />
            </div>
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 8px" }}>{t("forecast.hour_summary")}</h2>
              <MarginBars
                values={hourAvg}
                labels={Array.from({ length: 24 }, (_, h) => `${h}:00`)}
                testid="hr-bar"
                max={max}
                axisMin={t("forecast.axis_min")}
                sparseLabels
                onTip={onTip}
                onLeave={onLeave}
                onSelect={(h) => {
                  const v = hourAvg[h];
                  if (v == null) return;
                  setModal({ title: `${h}:00`, value: v, samples: cells.filter((c) => c.hour === h).reduce((a, c) => a + c.samples, 0), lowConf: false, ctx: ctxFor(v), swatch: delayColor(v) });
                }}
              />
            </div>
          </div>

          <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, maxWidth: 640, marginTop: 24 }}>{data.disclaimer}</p>
        </>
      )}

      <Tooltip tip={tip} />
      {modal && (
        <OverviewModal isOpen onClose={() => setModal(null)} title={modal.title}>
          <div style={{ fontSize: 11, letterSpacing: ".05em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
            {t("forecast.m_eyebrow")}
          </div>
          <div style={{ fontSize: 32, fontWeight: 600, fontVariantNumeric: "tabular-nums", margin: "2px 0 0" }}>
            <span aria-hidden style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: modal.swatch, marginRight: 8, verticalAlign: "middle" }} />
            {modal.value.toFixed(1)}
            <small style={{ fontSize: 14, color: "var(--text-secondary)", fontWeight: 400, marginLeft: 3 }}>{t("forecast.axis_min")}</small>
          </div>
          <p style={{ fontSize: 14, lineHeight: 1.6, margin: "12px 0 4px" }}>{modal.ctx}</p>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "0 0 4px" }}>
            {t("forecast.m_samples", { n: modal.samples.toLocaleString() })}
            {modal.lowConf ? t("forecast.m_low_conf") : ""}
          </p>
          {data?.disclaimer && (
            <p style={{ color: "var(--text-tertiary)", fontSize: 11, lineHeight: 1.5, marginTop: 12 }}>{data.disclaimer}</p>
          )}
        </OverviewModal>
      )}
    </div>
  );
}
