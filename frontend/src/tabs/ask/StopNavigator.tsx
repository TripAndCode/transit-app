import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { StopEvidence } from "./stopEvidence";

export function StopNavigator({ points, start, size, low, high, onStart, onSize, onPick }: {
  points: StopEvidence[]; start: number; size: number; low: number; high: number;
  onStart: (start: number) => void; onSize: (size: number) => void; onPick: (index: number) => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const normalize = (text: string) => text.normalize("NFKC").toLocaleLowerCase().trim();
  const query = normalize(search);
  const matches = points.map((point, index) => ({ point, index })).filter(({ point }) =>
    normalize(`${point.name} ${point.stopId ?? ""} ${point.sequence}`).includes(query));
  const total = points.length;
  const end = Math.min(total, start + size);
  return <div className="stop-navigator">
    <div className="stop-navigator-controls">
      <label>{t("ask.evidence.search")}
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
      </label>
      <label>{t("ask.evidence.window_size")}
        <select value={size} onChange={(event) => onSize(Number(event.target.value))}>
          {[...new Set([Math.min(8, total), Math.min(16, total), total])].map((count) =>
            <option key={count} value={count}>{count === total ? t("ask.evidence.all_stops") : t("ask.evidence.stop_count", { count })}</option>)}
        </select>
      </label>
      <span role="status">{t("ask.evidence.window", { start: start + 1, end, total })}</span>
    </div>
    {query && <div className="stop-search-results">
      {matches.length === 0 ? <p role="status">{t("ask.evidence.no_match")}</p> : matches.slice(0, 10).map(({ point, index }) =>
        <button key={index} type="button" onClick={() => { onPick(index); setSearch(""); }}>{point.name} · #{point.sequence}</button>)}
      {matches.length > 10 && <p>{t("ask.evidence.refine_search")}</p>}
    </div>}
    <div className="stop-navigator-strip">
      <div className="stop-navigator-mini" aria-hidden="true">
        {points.map((point, index) => <span key={index} className="stop-mini-slot">
          {point.minutes !== null && <span style={{ bottom: `${(Math.min(0, point.minutes) - low) / (high - low) * 100}%`,
            height: `${Math.abs(point.minutes) / (high - low) * 100}%`,
            background: index >= start && index < end ? "var(--accent)" : "var(--text-tertiary)" }} />}
        </span>)}
      </div>
      <span aria-hidden="true" className="stop-navigator-window" style={{ left: `${start / total * 100}%`, width: `${(end - start) / total * 100}%` }} />
      <input type="range" min={0} max={Math.max(0, total - size)} step={1} value={start}
        aria-label={t("ask.evidence.move_window")} aria-valuetext={t("ask.evidence.window", { start: start + 1, end, total })}
        disabled={total <= size} onChange={(event) => onStart(Number(event.target.value))} />
    </div>
    <div className="stop-navigator-endpoints"><span>{points[0]?.name}</span><span>{points.at(-1)?.name}</span></div>
  </div>;
}
