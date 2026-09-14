import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { StopEvidence, StopFocus } from "./stopEvidence";
import "./stopEvidence.css";

export function StopEvidenceChart({ messageId, points, onFocus }: {
  messageId: number;
  points: StopEvidence[];
  onFocus?: (focus: StopFocus | null) => void;
}) {
  const { t } = useTranslation();
  const [sequence, setSequence] = useState<number | null>(null);
  const selected = points.find((point) => point.sequence === sequence);
  const low = Math.min(0, ...points.map((point) => point.minutes));
  const high = Math.max(1, ...points.map((point) => point.minutes));
  const span = high - low;
  function select(point: StopEvidence | null) {
    setSequence(point?.sequence ?? null);
    onFocus?.(point ? { messageId, sequence: point.sequence, name: point.name } : null);
  }
  return (
    <section className="stop-evidence" aria-label={t("ask.evidence.title")}>
      <h3>{t("ask.evidence.title")}</h3>
      <p className="investigation-caption">{t("ask.evidence.scope")}</p>
      <div className="stop-evidence-layout">
        <div className="stop-evidence-scroll">
          <div className="stop-evidence-bars" role="group" aria-label={t("ask.evidence.select")}>
            {points.map((point) => (
              <button key={point.sequence} className="stop-evidence-column" type="button"
                aria-pressed={selected?.sequence === point.sequence}
                aria-label={t("ask.evidence.bar_label", { name: point.name, sequence: point.sequence, minutes: point.minutes, count: point.samples })}
                onClick={() => select(point)}>
                <span className="stop-evidence-plot">
                  <span className="stop-evidence-zero" style={{ bottom: `${-low / span * 100}%` }} />
                  <span className="stop-evidence-bar" style={{
                    bottom: `${(Math.min(0, point.minutes) - low) / span * 100}%`,
                    height: `${Math.abs(point.minutes) / span * 100}%`,
                  }} />
                  <span className="stop-evidence-value" style={{ bottom: `${(Math.max(0, point.minutes) - low) / span * 100}%` }}>{point.minutes.toLocaleString()}</span>
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
            <strong>{t("ask.evidence.minutes", { value: selected.minutes.toLocaleString() })}</strong>
            <p>{t("ask.evidence.metric")}</p>
            <p>{t("ask.evidence.samples", { count: selected.samples })}</p>
          </> : <p>{t("ask.evidence.select")}</p>}
          <p className="investigation-caption">{t("ask.evidence.caveat")}</p>
        </aside>
      </div>
    </section>
  );
}
