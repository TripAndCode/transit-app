import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConvMessage } from "../../api/types";
import { conditionsLabel, provenancePath, toolLabel } from "./provenance";
import type { StopEvidence, StopFocus } from "./stopEvidence";
import { StopNavigator } from "./StopNavigator";
import { Tooltip } from "../../components/Tooltip";
import { formatNumber } from "../../utils/format";
import "./stopEvidence.css";

export function StopEvidenceChart({ messageId, points, onFocus, complete = false, message }: {
  messageId: number;
  points: StopEvidence[];
  onFocus?: (focus: StopFocus | null) => void;
  complete?: boolean;
  /** The dispatched message this chart renders, for the provenance badge
   *  and the definition disclosure's route/conditions lines. Omitted by
   *  callers that don't have it in hand — the chart renders exactly as
   *  before with no provenance chrome in that case. */
  message?: ConvMessage;
}) {
  const { t } = useTranslation();
  const path = message ? provenancePath(message) : null;
  const label = message ? toolLabel(message.tool, t) : null;
  const route = typeof (message?.args?.route ?? message?.args?.route_code) === "string"
    ? String(message!.args!.route ?? message!.args!.route_code)
    : null;
  const detailId = useId();
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const layoutRef = useRef<HTMLDivElement | null>(null);
  const [sequence, setSequence] = useState<number | null>(null);
  const [detailLeft, setDetailLeft] = useState(0);
  useEffect(() => {
    if (sequence === null) return;
    const container = scrollRef.current;
    const button = container?.querySelector<HTMLButtonElement>('[aria-pressed="true"]');
    if (container && button) {
      selectedButtonRef.current = button;
      container.scrollLeft = button.offsetLeft - (container.clientWidth - button.offsetWidth) / 2;
    }
    // The popover is positioned in pixels relative to the actual scrolled
    // position of the selected button, not a static fraction of the window
    // size -- scrollLeft above re-centers the button whenever the strip
    // overflows its container, so a fraction-based left would drift away
    // from where the bar actually renders on screen.
    function updateDetailLeft() {
      const layout = layoutRef.current;
      const selectedButton = selectedButtonRef.current;
      if (layout && selectedButton) {
        setDetailLeft(selectedButton.getBoundingClientRect().left - layout.getBoundingClientRect().left);
      }
    }
    updateDetailLeft();
    container?.addEventListener("scroll", updateDetailLeft);
    function dismiss(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setSequence(null);
      onFocus?.(null);
      selectedButtonRef.current?.focus();
    }
    document.addEventListener("keydown", dismiss);
    return () => {
      document.removeEventListener("keydown", dismiss);
      container?.removeEventListener("scroll", updateDetailLeft);
    };
  }, [sequence, onFocus]);
  const [windowSize, setWindowSize] = useState(Math.min(8, points.length));
  const [windowStart, setWindowStart] = useState(0);
  const size = complete ? Math.min(windowSize, points.length) : points.length;
  const start = complete ? Math.max(0, Math.min(windowStart, points.length - size)) : 0;
  const visiblePoints = points.slice(start, start + size);
  const selected = points.find((point) => point.sequence === sequence);
  const rawLow = Math.min(0, ...points.flatMap((point) => point.minutes === null ? [] : [point.minutes]));
  const rawHigh = Math.max(1, ...points.flatMap((point) => point.minutes === null ? [] : [point.minutes]));
  const roughStep = (rawHigh - rawLow) / 3;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const step = ([1, 2, 5, 10].find((value) => value * magnitude >= roughStep) ?? 10) * magnitude;
  const low = Math.floor(rawLow / step) * step;
  const high = Math.ceil(rawHigh / step) * step;
  const span = high - low;
  const ticks = Array.from({ length: Math.round(span / step) + 1 }, (_, index) => low + index * step);
  function select(point: StopEvidence | null) {
    setSequence(point?.sequence ?? null);
    onFocus?.(point ? { messageId, sequence: point.sequence, name: point.name,
      ...(complete ? { patternId: point.patternId, stopId: point.stopId, rowIndex: point.rowIndex } : {}) } : null);
  }
  return (
    <section className="stop-evidence" aria-label={t("ask.evidence.title")}>
      {path && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 8,
            marginBottom: 4,
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          <span
            style={{
              fontWeight: 600,
              color: path === "sql" ? "var(--accent)" : "var(--text-secondary)",
              background: "var(--bg-soft)",
              border: "1px solid var(--border-soft)",
              borderRadius: 20,
              padding: "2px 9px",
            }}
          >
            {t(path === "sql" ? "ask.evidence.badge.sql" : "ask.evidence.badge.llm")}
          </span>
          {label && <span>{label}</span>}
        </div>
      )}
      <h3>{t(complete ? "ask.evidence.complete_title" : "ask.evidence.title")}</h3>
      <p className="investigation-caption">{t(complete ? "ask.evidence.pattern_short" : "ask.evidence.scope")}</p>
      <div className="stop-evidence-layout" ref={layoutRef}>
        <div ref={scrollRef} className="stop-evidence-scroll">
          <div className="stop-evidence-grid" aria-hidden="true">{ticks.map((tick) => <span key={tick}
            style={{ bottom: `${(tick - low) / span * 100}%` }}><i>{Number(tick.toPrecision(8))}</i></span>)}</div>
          <div className={`stop-evidence-bars${size > 16 ? " stop-evidence-overview" : ""}`} role="group" aria-label={t("ask.evidence.select")}>
            {visiblePoints.map((point) => (
              <Tooltip key={point.sequence} label={`${point.name} · #${point.sequence}`}>
                <button className="stop-evidence-column" type="button"
                  aria-pressed={selected?.sequence === point.sequence}
                  aria-controls={selected?.sequence === point.sequence ? detailId : undefined}
                  aria-label={t(point.minutes === null ? "ask.evidence.missing_label" : "ask.evidence.bar_label", { name: point.name, sequence: point.sequence, minutes: point.minutes, count: point.samples })}
                  onClick={(event) => { selectedButtonRef.current = event.currentTarget; select(point); }}>
                  <span className="stop-evidence-plot">
                    <span className="stop-evidence-zero" style={{ bottom: `${-low / span * 100}%` }} />
                    {point.minutes === null ? <span className="stop-evidence-missing">—<br />{t("ask.evidence.missing")}</span> : <>
                    <span className="stop-evidence-bar" style={{
                      bottom: `${(Math.min(0, point.minutes) - low) / span * 100}%`,
                      height: `${Math.abs(point.minutes) / span * 100}%`,
                    }} />
                    <span className="stop-evidence-value" style={{ bottom: `${(Math.max(0, point.minutes) - low) / span * 100}%` }}>{formatNumber(point.minutes)}</span>
                    </>}
                  </span>
                  <span className="stop-evidence-name">{point.name}</span>
                  <span className="investigation-caption">#{point.sequence}</span>
                </button>
              </Tooltip>
            ))}
          </div>
          <p className="investigation-caption">{t("ask.evidence.unit")}</p>
        </div>
        {selected && <aside id={detailId} className="stop-evidence-detail" role="region"
          aria-label={t("ask.evidence.selected_detail")} aria-live="polite"
          style={{ left: `clamp(0px, ${detailLeft}px, calc(100% - 250px))` }}>
            <button className="stop-evidence-clear" type="button" onClick={() => { select(null); selectedButtonRef.current?.focus(); }}>{t("ask.evidence.clear")}</button>
            <h3>{selected.name}</h3>
            <strong>{selected.minutes === null ? "—" : t("ask.evidence.minutes", { value: formatNumber(selected.minutes) })}</strong>
            {selected.minutes === null && <p>{t("ask.evidence.missing")}</p>}
            <p>{t("ask.evidence.samples", { count: selected.samples })}</p>
            <details><summary>{t("ask.evidence.values")}</summary>
              <dl><dt>{t("ask.evidence.sequence")}</dt><dd>{selected.sequence}</dd>
              {selected.stopId && <><dt>{t("ask.evidence.stop_id")}</dt><dd>{selected.stopId}</dd></>}</dl>
            </details>
        </aside>}
      </div>
      {complete && points.length > 0 && <StopNavigator points={points} start={start} size={size} low={low} high={high}
        onStart={(next) => { setWindowStart(next); select(null); }}
        onSize={(next) => { setWindowSize(next); select(null); }}
        onPick={(index) => { setWindowSize(Math.min(8, points.length)); setWindowStart(Math.max(0, index - 3)); select(points[index]); }} />}
      <details className="stop-evidence-definition"><summary>{t("ask.evidence.definition")}</summary>
        <p>{t(complete ? "ask.evidence.complete_scope" : "ask.evidence.scope")}</p>
        <p>{t(complete ? "ask.evidence.pattern_caveat" : "ask.evidence.caveat")}</p>
        {message && (
          <dl style={{ margin: "6px 0 0", display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px" }}>
            {route && (
              <>
                <dt>{t("ask.evidence.disclosure_route")}</dt>
                <dd>{route}</dd>
              </>
            )}
            <dt>{t("ask.evidence.disclosure_conditions")}</dt>
            <dd>{conditionsLabel(message.conditions, t)}</dd>
            {label && (
              <>
                <dt>{t("ask.evidence.disclosure_aggregation")}</dt>
                <dd>{label}</dd>
              </>
            )}
            <dt>{t("ask.evidence.disclosure_confidence")}</dt>
            <dd>{t(path === "sql" ? "ask.evidence.confidence.sql" : "ask.evidence.confidence.llm")}</dd>
          </dl>
        )}
      </details>
    </section>
  );
}
