import { useTranslation } from "react-i18next";
import type { RouteShapeStop } from "../../api/types";
import { matchedPrevious } from "./stopSeries";
import { delayColor } from "../../styles/tokens";
import { ChartAxis } from "./ChartAxis";

export function StopChart({ stops, previous, selected, onSelect }: {
  stops: RouteShapeStop[]; previous: RouteShapeStop[]; selected: number; onSelect: (sequence: number) => void;
}) {
  const { t } = useTranslation("design");
  const low = Math.min(0, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const high = Math.max(1, ...stops.map((s) => s.avg_min ?? 0), ...previous.map((s) => s.avg_min ?? 0));
  const x = (i: number) => 52 + i / Math.max(1, stops.length - 1) * 700;
  const y = (n: number) => 280 - (n - low) / (high - low) * 240;
  function path(values: (number | null)[]) {
    return values.map((v, i) => v == null ? "" : `${i === 0 || values[i - 1] == null ? "M" : "L"}${x(i)},${y(v)}`).join(" ");
  }
  return <svg className="focus-chart" viewBox="0 0 780 335" role="group" aria-label={t("stopDelay")}>
    <ChartAxis low={low} high={high} y={y} />
    <path d={path(stops.map((s) => matchedPrevious(s, previous)))} fill="none" stroke="var(--text-secondary)" strokeWidth={2} strokeDasharray="5 5" />
    <path d={path(stops.map((s) => s.avg_min))} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
    {stops.map((s, i) => <g key={`${s.stop_sequence}-${s.stop_id}`}>
      <circle cx={x(i)} cy={s.avg_min == null ? 280 : y(s.avg_min)} r={s.stop_sequence === selected ? 7 : 4}
        fill={s.avg_min == null ? "var(--bg-surface)" : delayColor(s.avg_min)} stroke="var(--text-secondary)"
        role="button" tabIndex={0} aria-label={`${s.stop_name}: ${s.avg_min == null ? t("missing") : `${s.avg_min} ${t("minutes")}`}`}
        onClick={() => onSelect(s.stop_sequence)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(s.stop_sequence); } }}>
        <title>{s.stop_name}</title>
      </circle>
      {(i % Math.max(1, Math.ceil(stops.length / 7)) === 0 || i === stops.length - 1) &&
        <text x={x(i)} y={307} textAnchor="middle" fill="var(--text-secondary)" fontSize={11}>{s.stop_name.slice(0, 7)}</text>}
    </g>)}
  </svg>;
}
