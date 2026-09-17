import { useState } from "react";
import { Info, Radio, X } from "lucide-react";
import type { TFunction } from "i18next";
import { LegendChip } from "../../components/LegendChip";
import { readMapReferencePref, writeMapReferencePref } from "./mapReferencePref";

/** The operations map's reference panel: what the markers mean, and what they
 * are.
 *
 * These were two separate floating boxes, with the marker disclosure pinned
 * at a fixed `top` on the opposite side of the map — landing in open water,
 * anchored to nothing. They are the same kind of content: text you read once
 * to interpret the map, not a control you act on. One box holds both, stacked
 * under the filter dock, and collapses to a single icon so the map can be
 * cleared for watching.
 */
export function MapReference({ located, total, t }: {
  located: number;
  total: number;
  t: TFunction;
}) {
  const [open, setOpen] = useState(readMapReferencePref);

  function toggle(next: boolean) {
    setOpen(next);
    writeMapReferencePref(next);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="ops-map-ref__peek tip tip--below"
        data-tip={t("operations.map.reference_show")}
        aria-label={t("operations.map.reference_show")}
        onClick={() => toggle(true)}
      >
        <Info size={15} aria-hidden="true" />
      </button>
    );
  }

  return (
    <div className="ops-map-ref" aria-label={t("operations.map.legend_label")}>
      <div className="ops-map-ref__legend">
        <LegendChip color="var(--accent-strong)" label={t("operations.map.legend_current")} />
        <LegendChip color="#2bc5aa" label={t("operations.map.legend_trail")} />
        <LegendChip color="var(--delay-flag)" label={t("operations.map.legend_delay")} />
        <LegendChip color="#2bc5aa" label={t("operations.map.legend_cluster")} />
      </div>
      {/* States what the markers are, and nothing else: the reading's age is
          the header freshness dot's job, and repeating it here put the same
          fact on screen three times. */}
      <p className="ops-map-ref__note">
        <Radio size={13} aria-hidden="true" />
        <span>{t("operations.map.disclosure", { located, total })}</span>
      </p>
      <button
        type="button"
        className="ops-map-ref__hide tip tip--below"
        data-tip={t("operations.map.reference_hide")}
        aria-label={t("operations.map.reference_hide")}
        onClick={() => toggle(false)}
      >
        <X size={13} aria-hidden="true" />
      </button>
    </div>
  );
}
