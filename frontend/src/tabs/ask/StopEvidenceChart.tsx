import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { StopEvidence, StopFocus } from "./stopEvidence";
import { StopNavigator } from "./StopNavigator";
import "./stopEvidence.css";

export function StopEvidenceChart({ messageId, points, onFocus, complete = false }: {
  messageId: number;
  points: StopEvidence[];
  onFocus?: (focus: StopFocus | null) => void;
  complete?: boolean;
}) {
  const { t } = useTranslation();
  const [sequence, setSequence] = useState<number | null>(null);
  const [windowSize, setWindowSize] = useState(Math.min(8, points.length));
  const [windowStart, setWindowStart] = useState(0);
  const size = complete ? Math.min(windowSize, points.length) : points.length;
  const start = complete ? Math.max(0, Math.min(windowStart, points.length - size)) : 0;
  const visiblePoints = points.slice(start, start + size);
  const selected = points.find((point) => point.sequence === sequence);
  const low = Math.min(0, ...points.flatMap((point) => point.minutes === null ? [] : [point.minutes]));
  const high = Math.max(1, ...points.flatMap((point) => point.minutes === null ? [] : [point.minutes]));
  const span = high - low;
  function select(point: StopEvidence | null) {
    setSequence(point?.sequence ?? null);
    onFocus?.(point ? { messageId, sequence: point.sequence, name: point.name,
      ...(complete ? { patternId: point.patternId, stopId: point.stopId, rowIndex: point.rowIndex } : {}) } : null);
  }
  return (
    <section className="stop-evidence" aria-label={t("ask.evidence.title")}>
      <h3>{t(complete ? "ask.evidence.complete_title" : "ask.evidence.title")}</h3>
      <p className="investigation-caption">{t(complete ? "ask.evidence.complete_scope" : "ask.evidence.scope")}</p>
      <div className="stop-evidence-layout">
        <div className="stop-evidence-scroll">
          <div className={`stop-evidence-bars${size > 16 ? " stop-evidence-overview" : ""}`} role="group" aria-label={t("ask.evidence.select")}>
            {visiblePoints.map((point) => (
              <button key={point.sequence} className="stop-evidence-column" type="button"
                aria-pressed={selected?.sequence === point.sequence}
                title={`${point.name} · #${point.sequence}`}
                aria-label={t(point.minutes === null ? "ask.evidence.missing_label" : "ask.evidence.bar_label", { name: point.name, sequence: point.sequence, minutes: point.minutes, count: point.samples })}
                onClick={() => select(point)}>
                <span className="stop-evidence-plot">
                  <span className="stop-evidence-zero" style={{ bottom: `${-low / span * 100}%` }} />
                  {point.minutes === null ? <span className="stop-evidence-missing">—<br />{t("ask.evidence.missing")}</span> : <>
                  <span className="stop-evidence-bar" style={{
                    bottom: `${(Math.min(0, point.minutes) - low) / span * 100}%`,
                    height: `${Math.abs(point.minutes) / span * 100}%`,
                  }} />
                  <span className="stop-evidence-value" style={{ bottom: `${(Math.max(0, point.minutes) - low) / span * 100}%` }}>{point.minutes.toLocaleString()}</span>
                  </>}
                </span>
                <span className="stop-evidence-name">{point.name}</span>
                <span className="investigation-caption">#{point.sequence}</span>
              </button>
            ))}
          </div>
          <p className="investigation-caption">{t("ask.evidence.unit")}</p>
        </div>
        <aside className="stop-evidence-detail" aria-live="polite">
          {selected ? <>
            <button className="stop-evidence-clear" type="button" onClick={() => select(null)}>{t("ask.evidence.clear")}</button>
            <h3>{selected.name}</h3>
            <strong>{selected.minutes === null ? "—" : t("ask.evidence.minutes", { value: selected.minutes.toLocaleString() })}</strong>
            {selected.minutes === null && <p>{t("ask.evidence.missing")}</p>}
            <p>{t("ask.evidence.metric")}</p>
            <p>{t("ask.evidence.samples", { count: selected.samples })}</p>
          </> : <p>{t("ask.evidence.select")}</p>}
          <p className="investigation-caption">{t(complete ? "ask.evidence.pattern_caveat" : "ask.evidence.caveat")}</p>
        </aside>
      </div>
      {complete && points.length > 0 && <StopNavigator points={points} start={start} size={size}
        onStart={(next) => { setWindowStart(next); select(null); }}
        onSize={(next) => { setWindowSize(next); select(null); }}
        onPick={(index) => { setWindowSize(Math.min(8, points.length)); setWindowStart(Math.max(0, index - 3)); select(points[index]); }} />}
    </section>
  );
}
