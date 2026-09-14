import { useTranslation } from "react-i18next";
import type { TrendDay } from "../../api/types";

export function PeriodChart({ days }: { days: TrendDay[] }) {
  const { t } = useTranslation("design");
  const values = days.filter((d) => Number.isFinite(d.avg_min) && d.samples > 0);
  const low = Math.min(0, ...values.map((d) => d.avg_min));
  const high = Math.max(1, ...values.map((d) => d.avg_min));
  const first = Date.parse(days[0]?.date ?? "");
  const span = Math.max(86400000, Date.parse(days.at(-1)?.date ?? "") - first);
  const x = (date: string) => 52 + (Date.parse(date) - first) / span * 700;
  const y = (v: number) => 225 - (v - low) / (high - low) * 190;
  // Break the line across missing calendar days. No invented zero or interpolation.
  const path = values.map((d, i) => `${i && Date.parse(d.date) - Date.parse(values[i - 1].date) === 86400000 ? "L" : "M"}${x(d.date)},${y(d.avg_min)}`).join(" ");
  return <svg className="focus-chart" viewBox="0 0 780 280" role="img" aria-label={t("trend")}>
    {[0, 1, 2, 3, 4].map((tick) => {
      const value = low + (high - low) * tick / 4;
      return <g key={tick}><line x1={52} x2={752} y1={y(value)} y2={y(value)} stroke="var(--border-subtle)" strokeDasharray="2 4" /><text x={42} y={y(value) + 4} textAnchor="end" fontSize={12} fill="var(--text-secondary)">{value.toFixed(1)}</text></g>;
    })}
    <path d={path} stroke="var(--accent)" strokeWidth={2} fill="none" />
    {values.map((d, i) => <g key={d.date}><circle cx={x(d.date)} cy={y(d.avg_min)} r={4} fill="var(--accent)"><title>{d.date}: {d.avg_min} {t("minutes")} ({d.samples})</title></circle>
      {(i % Math.max(1, Math.ceil(values.length / 7)) === 0 || i === values.length - 1) && <text x={x(d.date)} y={250} textAnchor="middle" fill="var(--text-secondary)" fontSize={12}>{d.date.slice(5)}</text>}
    </g>)}
  </svg>;
}
