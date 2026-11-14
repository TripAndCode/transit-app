import { useTranslation } from "react-i18next";
import type { RouteShapeStop } from "../../api/types";
import { SHARED_TABLE, td, th } from "../tableStyles";
import { fixedTenth, formatDateRange, formatNumber } from "../../utils/format";
import { DelayBandLegend, StopChart } from "./StopChart";
import { missingStopKey, previousWindow, windowDays, type SegmentRise } from "./routeDossier";
import "../../styles/focusedAnalysis.css";

// A route's delay by stop, as its page draws it from one route-shape
// response: fetched live, or stored by a snapshot. Each part takes the stops
// and the reader's choices as props and fetches nothing.

/** Where delay grows most along the route, and the next largest. */
export function RiseHeadline({ rise, nextRise }: { rise: SegmentRise | undefined; nextRise: SegmentRise | undefined }) {
  const { t } = useTranslation("design");
  if (!rise) return null;
  return <div className="route-rise">
    <p className="route-rise__headline">{t("riseHeadline", { from: rise.from.stop_name, to: rise.to.stop_name, min: fixedTenth(rise.increaseMin) })}</p>
    {nextRise && <p className="focus-muted">{t("riseNext", { from: nextRise.from.stop_name, to: nextRise.to.stop_name, min: fixedTenth(nextRise.increaseMin) })}</p>}
  </div>;
}

/** The mean delay at each stop over `from`..`to`, with the equal-length
 *  window before it drawn beside when `compare` is on. */
export function StopDelayTrend({
  stops,
  previous,
  compare,
  selected,
  onSelect,
  rise,
  from,
  to,
}: {
  stops: RouteShapeStop[];
  previous: RouteShapeStop[];
  compare: boolean;
  selected: number;
  onSelect: (sequence: number) => void;
  rise: SegmentRise | undefined;
  from: string;
  to: string;
}) {
  const { t } = useTranslation("design");
  const previousLabel = t("previous", { days: windowDays(from, to) });
  const prior = previousWindow(from, to);
  return <>
    <div className="focus-actions focus-muted"><span style={{ color: "var(--accent)" }}>― {t("selected")}</span>{compare && <span>┄ {previousLabel}</span>}{rise && <span><span aria-hidden="true" className="route-rise__swatch" /> {t("riseHighlight")}</span>}<span>○ {t("missing")}</span><DelayBandLegend /></div>
    <p className="focus-muted focus-chart-hint">{t("chartScrollHint", { count: stops.length })}</p>
    <div className="focus-chart-scroll"><StopChart stops={stops} previous={previous} selected={selected} onSelect={onSelect} highlight={rise ? { from: rise.from.stop_sequence, to: rise.to.stop_sequence } : null} /></div>
    <p className="focus-muted">{t("selected")} {formatDateRange(from, to)}{compare && ` · ${previousLabel} ${formatDateRange(prior.from, prior.to)}`}</p>
  </>;
}

/** Every stop's mean delay and observations, in stop order. */
export function StopDelayTable({ stops }: { stops: RouteShapeStop[] }) {
  const { t } = useTranslation("design");
  return <div className="focus-table-wrap"><table style={SHARED_TABLE}><thead><tr><th style={th()}>{t("stop")}</th><th style={th()}>{t("mean")}</th><th style={th()}>{t("samples")}</th></tr></thead><tbody>
    {stops.map((s) => <tr key={s.stop_sequence}><td style={td()}>{s.stop_name}</td><td style={td()}>{s.avg_min == null ? t(missingStopKey(s, stops)) : fixedTenth(s.avg_min)}</td><td style={td()}>{formatNumber(s.samples)}</td></tr>)}
  </tbody></table></div>;
}

/** The chosen stop's own mean delay and observations, and a picker for it. */
export function SelectedStopAside({
  stops,
  selected,
  onSelect,
}: {
  stops: RouteShapeStop[];
  selected: RouteShapeStop | undefined;
  onSelect: (sequence: number) => void;
}) {
  const { t } = useTranslation("design");
  return <aside className="focus-aside"><label>{t("selectedStop")}<select style={{ width: "100%", margin: "12px 0" }} value={selected?.stop_sequence ?? ""} onChange={(e) => onSelect(Number(e.target.value))}>
    {stops.map((s) => <option key={s.stop_sequence} value={s.stop_sequence}>{s.stop_name}</option>)}
  </select></label><p><strong className="num">{selected?.avg_min == null ? "—" : fixedTenth(selected.avg_min)}</strong> {t("minutes")}</p><p>{t("samples")} {formatNumber(selected?.samples ?? 0)}</p></aside>;
}
