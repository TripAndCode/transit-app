import type { TFunction } from "i18next";
import type { TimeBand } from "../api/rangeContext";

/**
 * The 8 time-band filter choices, in display order. Single source of truth
 * for FilterContextBar.tsx and TabFilterBar.tsx, which both render this same
 * picker — kept as one function (mirroring askCardTemplates.ts's
 * buildCardTemplates() pattern) so the two pickers can't silently diverge.
 */
export function buildTimeBandOptions(t: TFunction): { value: TimeBand; label: string }[] {
  return [
    { value: "all", label: t("filters.time_band.all") },
    { value: "morning", label: t("filters.time_band.morning") },
    { value: "forenoon", label: t("filters.time_band.forenoon") },
    { value: "noon", label: t("filters.time_band.noon") },
    { value: "afternoon", label: t("filters.time_band.afternoon") },
    { value: "evening", label: t("filters.time_band.evening") },
    { value: "night", label: t("filters.time_band.night") },
    { value: "late_night", label: t("filters.time_band.late_night") },
  ];
}
