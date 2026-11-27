import { useTranslation } from "react-i18next";
import type { RouteReferences } from "../../api/types";
import { InsightHint } from "../InsightHint";
import { formatNumber } from "../../utils/format";
import { FLAT_MAX_DELTA_MIN, FLAT_MAX_DELTA_PCT, FLAT_MAX_DELTA_PP } from "../overview/storySentence";
import { judge, rangeOf, reasonText, valueText, type ReferenceFigure } from "./routeReferenceText";
import "./RouteReference.css";

/** The rules behind a route's reference. A scope with no aggregate read no
 *  days, so its counts are 0 and the floors they would state are left out. */
function ReferenceHint({ refs }: { refs: RouteReferences }) {
  const { t } = useTranslation();
  const coverage =
    refs.reason === "not_aggregated"
      ? ""
      : t("routeReference.hint_coverage", {
          selected: refs.days_selected,
          min_days: refs.min_days,
          min_samples: formatNumber(refs.min_samples),
        });
  return (
    <InsightHint
      title={t("overview.reference.hint_title", { days: refs.days })}
      body={t("routeReference.hint_body", {
        days: refs.days,
        range: rangeOf(refs),
        coverage,
        flat_pp: FLAT_MAX_DELTA_PP,
        flat_min: FLAT_MAX_DELTA_MIN,
        flat_pct: FLAT_MAX_DELTA_PCT,
      })}
    />
  );
}

/** The line above the Routes table naming what its marks compare each route's
 *  on-time rate with, or, when no route can be compared, why. */
export function RouteReferenceLegend({ refs, timeBand }: { refs: RouteReferences; timeBand: string }) {
  const { t } = useTranslation();
  const text =
    reasonText(refs, refs, timeBand, t) ??
    t("routeReference.legend", { days: refs.days, range: rangeOf(refs), min: formatNumber(refs.min_samples) });
  return (
    <p className="route-reference route-reference--legend">
      <span>{text}</span>
      <ReferenceHint refs={refs} />
    </p>
  );
}

/** A route's reference beside one of its page's figures: the reference's own
 *  figure and a judgement against it, or why it has none. A page with several
 *  notes states the reason and the rules' popover once: `primary` marks the
 *  note that does. */
export function RouteReferenceNote({
  refs,
  route,
  figure,
  value,
  timeBand,
  primary = false,
}: {
  refs: RouteReferences;
  route: string;
  figure: ReferenceFigure;
  value: number | null;
  timeBand: string;
  primary?: boolean;
}) {
  const { t } = useTranslation();
  const ref = refs.routes[route];
  if (ref == null || value == null || (ref.reason != null && !primary)) return null;
  const own = figure === "avg" ? ref.avg_min : ref.on_time_pct;
  const judgement = ref.reason == null ? judge(figure, value, ref) : null;
  return (
    <span className="route-reference">
      <span>{reasonText(refs, ref, timeBand, t) ?? (own == null ? null : valueText(refs, own, figure, t))}</span>
      {judgement && (
        <span className="reference-chip">{t(`overview.reference.judgement_${judgement}`, { days: refs.days })}</span>
      )}
      {primary && <ReferenceHint refs={refs} />}
    </span>
  );
}
