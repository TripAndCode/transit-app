import { useTranslation } from "react-i18next";
import type { DefinitionMeta } from "../api/types";

type Props = {
  definition: DefinitionMeta;
};

/** Small always-visible strip stating which on-time/late tolerance, dedup
 *  rule, and exclusion threshold produced the numbers shown next to it --
 *  so a user comparing two agencies' (or two exports') on-time rates can
 *  see they're using the same definition instead of assuming it.
 *  Renders on every report/comparison view that shows on-time-style
 *  figures; values come straight from the API response's `definition`
 *  field (pipeline/reports/definition.py is the source of truth), never
 *  hardcoded here. */
export function DefinitionMetaBlock({ definition }: Props) {
  const { t } = useTranslation();
  const early =
    definition.early_tolerance_sec == null
      ? t("definitionMeta.unbounded")
      : t("definitionMeta.seconds", { value: definition.early_tolerance_sec });
  const late =
    definition.late_tolerance_sec == null
      ? t("definitionMeta.not_applicable")
      : t("definitionMeta.seconds", { value: definition.late_tolerance_sec });

  return (
    <div
      data-testid="definition-meta"
      style={{
        fontSize: 11.5,
        color: "var(--text-tertiary)",
        lineHeight: 1.6,
        margin: "2px 0 14px",
      }}
    >
      {definition.preset != null && (
        <>
          {t("definitionMeta.tolerance_line", { preset: definition.preset, early, late })}
          {" · "}
        </>
      )}
      {t("definitionMeta.measurement_point")}
      {" · "}
      {t("definitionMeta.dedup_rule")}
      {" · "}
      {t("definitionMeta.exclusion_rule", { sec: definition.exclusion_threshold_sec })}
    </div>
  );
}
