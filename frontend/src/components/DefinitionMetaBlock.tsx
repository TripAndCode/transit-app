import { useTranslation } from "react-i18next";
import type { DefinitionMeta } from "../api/types";
import { formatDuration } from "../utils/format";

type Props = {
  definition: DefinitionMeta;
};

/** How the figures beside it were made, in plain words, behind one
 *  disclosure: which departures count, how a repeated report is treated,
 *  which readings are dropped, and what on time means. A reader comparing
 *  two agencies or two exports can open it to see they share a definition.
 *  Every value comes from the API response's `definition` field
 *  (pipeline/reports/definition.py is the source of truth); an identifier
 *  this build has no words for is shown as the API sent it, so text and
 *  definition can never silently drift apart. */
export function DefinitionMetaBlock({ definition }: Props) {
  const { t } = useTranslation();
  const exclusion = definition.exclusion_threshold_sec;
  const sentences = [
    t(`definitionMeta.measurementPoint.${definition.measurement_point}`, { defaultValue: definition.measurement_point }),
    t(`definitionMeta.dedupRule.${definition.dedup_rule}`, { defaultValue: definition.dedup_rule }),
    exclusion % 3600 === 0
      ? t("definitionMeta.exclusion_hours", { count: exclusion / 3600 })
      : t("definitionMeta.exclusion_limit", { limit: formatDuration(exclusion) }),
  ];
  if (definition.late_tolerance_sec != null) {
    const late = formatDuration(definition.late_tolerance_sec);
    sentences.push(
      definition.early_tolerance_sec == null
        ? t("definitionMeta.on_time_late", { late })
        : t("definitionMeta.on_time_late_early", { late, early: formatDuration(definition.early_tolerance_sec) }),
    );
  }

  return (
    <details data-testid="definition-meta" className="definition-meta" style={{ margin: "2px 0 14px" }}>
      <summary>{t("definitionMeta.summary")}</summary>
      <p>{sentences.join(t("definitionMeta.sentence_separator"))}</p>
    </details>
  );
}
