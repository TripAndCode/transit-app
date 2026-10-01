import type { TFunction } from "i18next";
import type { TimeBand } from "../api/scope";

/**
 * The 8 time-band filter choices, in display order. Single source of truth
 * for every band picker (Ask's FilterContextBar, the scope sentence's time
 * control, the command palette), kept as one function so they can't
 * silently diverge.
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
