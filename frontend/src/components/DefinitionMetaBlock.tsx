import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { DefinitionMeta, TimetableVersion } from "../api/types";
import { toJstISO } from "../api/scope";
import { formatDate, formatDuration } from "../utils/format";

type Props = {
  definition: DefinitionMeta;
};

type BlockProps = Props & {
  /** Each agency's name, for figures that span several agencies, whose
   *  timetables are then listed under their agency's name. */
  agencyNames?: ReadonlyMap<number, string>;
};

/** How the figures beside it were made, in plain words, behind one
 *  disclosure: what they are made from, which departures count, how a
 *  repeated report is treated, which readings are dropped, what on time means
 *  and, where the figures carry an early-departure rate, what it counts, then
 *  the method version, the day the aggregates were built and the timetables
 *  in force. A reader comparing two agencies or two exports can open it to see
 *  they share a definition. Every value comes from the API response's
 *  `definition` field (pipeline/reports/definition.py is the source of
 *  truth); an identifier this build has no words for is shown as the API
 *  sent it, so text and definition can never silently drift apart. */
export function DefinitionMetaBlock({ definition, agencyNames }: BlockProps) {
  const { t } = useTranslation();
  const exclusion = definition.exclusion_threshold_sec;
  const sentences = [
    t(`definitionMeta.valueBasis.${definition.value_basis}`, { defaultValue: definition.value_basis }),
    t(`definitionMeta.measurementPoint.${definition.measurement_point}`, { defaultValue: definition.measurement_point }),
    t(`definitionMeta.dedupRule.${definition.dedup_rule}`, { defaultValue: definition.dedup_rule }),
    exclusion % 3600 === 0
      ? t("definitionMeta.exclusion_hours", { count: exclusion / 3600 })
      : t("definitionMeta.exclusion_limit", { limit: formatDuration(exclusion) }),
  ];
  const onTime = onTimeSentence(definition, t);
  if (onTime != null) sentences.push(onTime);
  if (definition.early_departure_sec != null) {
    sentences.push(t("definitionMeta.early_departure", { early: formatDuration(definition.early_departure_sec) }));
  }

  return (
    <details data-testid="definition-meta" className="definition-meta" style={{ margin: "2px 0 14px" }}>
      <summary>{t("definitionMeta.summary")}</summary>
      <p>{sentences.join(t("definitionMeta.sentence_separator"))}</p>
      <p>{methodSentence(definition, t)}</p>
      {definition.timetable_versions != null && definition.timetable_versions.length > 0 && (
        <Timetables versions={definition.timetable_versions} agencyNames={agencyNames} />
      )}
    </details>
  );
}

/** The timetables in force, with the days each covers: one sentence for one
 *  agency, and a list under each agency's name for several. */
function Timetables({ versions, agencyNames }: { versions: TimetableVersion[]; agencyNames?: ReadonlyMap<number, string> }) {
  const { t } = useTranslation();
  const byAgency = groupByAgency(versions);
  if (byAgency.size <= 1) {
    return <p>{t("definitionMeta.timetable", timetableParts(versions, t))}</p>;
  }
  return (
    <>
      <p>{t("definitionMeta.timetable_heading")}</p>
      <ul>
        {[...byAgency].map(([agencyId, own]) => (
          <li key={agencyId}>
            {t("definitionMeta.timetable_agency", {
              agency: agencyNames?.get(agencyId) ?? String(agencyId),
              ...timetableParts(own, t),
            })}
          </li>
        ))}
      </ul>
    </>
  );
}

function groupByAgency(versions: TimetableVersion[]): Map<number, TimetableVersion[]> {
  const byAgency = new Map<number, TimetableVersion[]>();
  for (const version of versions) {
    const own = byAgency.get(version.agency_id);
    if (own) own.push(version);
    else byAgency.set(version.agency_id, [version]);
  }
  return byAgency;
}

/** One agency's timetables as the `versions` and the range's day `count` the
 *  timetable sentences interpolate. A version is named by the day its zip was
 *  fetched where its name carries one, else by its name. */
function timetableParts(versions: TimetableVersion[], t: TFunction): { versions: string; count: number } {
  const parts = versions.map((version) => {
    if (version.name == null) return t("definitionMeta.timetable_unrecorded", { count: version.days });
    if (version.fetched_on != null) {
      return t("definitionMeta.timetable_fetched", { count: version.days, day: formatDate(version.fetched_on) });
    }
    return t("definitionMeta.timetable_named", { count: version.days, name: version.name });
  });
  const count = versions.reduce((days, version) => days + version.days, 0);
  return { versions: parts.join(t("definitionMeta.timetable_separator")), count };
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

/** The method version and the day its aggregates were last built: the JST
 *  day, the one every date in the app is, never the viewer's own. A live scan
 *  has no build day, which the stamp says rather than leaving it out, as it
 *  says when an aggregate view has no build recorded. */
function methodSentence(definition: DefinitionMeta, t: TFunction): string {
  return t("definitionMeta.method_version", { version: definition.method_version, day: builtDay(definition, t) });
}

function builtDay(definition: DefinitionMeta, t: TFunction): string {
  if (definition.source === "live") return t("definitionMeta.built_day_live");
  const built = definition.built_at == null ? null : new Date(definition.built_at);
  if (built == null || isNaN(built.getTime())) return t("definitionMeta.built_day_unknown");
  return formatDate(toJstISO(built));
}
