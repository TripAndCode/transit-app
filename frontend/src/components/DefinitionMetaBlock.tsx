import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { DefinitionMeta } from "../api/types";
import { toJstISO } from "../api/scope";
import { formatDate, formatDuration } from "../utils/format";

type Props = {
  definition: DefinitionMeta;
};

/** How the figures beside it were made, in plain words, behind one
 *  disclosure: which departures count, how a repeated report is treated,
 *  which readings are dropped, and what on time means, then the method
 *  version and the day the aggregates were built. A reader comparing two
 *  agencies or two exports can open it to see they share a definition.
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
  const onTime = onTimeSentence(definition, t);
  if (onTime != null) sentences.push(onTime);

  return (
    <details data-testid="definition-meta" className="definition-meta" style={{ margin: "2px 0 14px" }}>
      <summary>{t("definitionMeta.summary")}</summary>
      <p>{sentences.join(t("definitionMeta.sentence_separator"))}</p>
      <p>{methodSentence(definition, t)}</p>
    </details>
  );
}

/** What the rows counted as on time, kept in view beside a list of on-time
 *  rates, which mean nothing without it. */
export function OnTimeDefinition({ definition }: Props) {
  const { t } = useTranslation();
  const sentence = onTimeSentence(definition, t);
  return sentence == null ? null : <p className="on-time-definition">{sentence}</p>;
}

/** The definition's on-time rule, or null for a report without one. An
 *  unbounded early side lets every early departure count as on time, which
 *  the sentence says outright: a reader otherwise assumes a window. */
function onTimeSentence(definition: DefinitionMeta, t: TFunction): string | null {
  if (definition.late_tolerance_sec == null) return null;
  const late = formatDuration(definition.late_tolerance_sec);
  return definition.early_tolerance_sec == null
    ? t("definitionMeta.on_time_late", { late })
    : t("definitionMeta.on_time_late_early", { late, early: formatDuration(definition.early_tolerance_sec) });
}

/** The method version, with the day its aggregates were last built: the JST
 *  day, the one every date in the app is, never the viewer's own. */
function methodSentence(definition: DefinitionMeta, t: TFunction): string {
  const version = definition.method_version;
  const built = definition.built_at == null ? null : new Date(definition.built_at);
  if (built == null || isNaN(built.getTime())) return t("definitionMeta.method_version", { version });
  return t("definitionMeta.method_version_built", { version, date: formatDate(toJstISO(built)) });
}
