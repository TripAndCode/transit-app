import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { DefinitionMeta, FeedQuality, SourceMix, TimetableVersion } from "../api/types";
import { PRESET_NAMES } from "../api/onTimeDefault";
import { useCapabilityTierOf, type TierState } from "../api/useCapabilityTier";
import { toJstISO } from "../api/scope";
import { fixedTenth, formatDate, formatDuration, formatNumber } from "../utils/format";

type Props = {
  definition: DefinitionMeta;
};

type BlockProps = Props & {
  /** Each agency's name, for figures that span several agencies, whose
   *  timetables, sources and feed quality are then listed under their
   *  agency's name. */
  agencyNames?: ReadonlyMap<number, string>;
  /** Each agency's capability tier, for a page that can't read the agency
   *  catalogue; otherwise the catalogue's tiers are read. */
  tiers?: ReadonlyMap<number, TierState>;
};

/** How the figures beside it were made, in plain words, behind one
 *  disclosure: what they are made from, which departures count, how a
 *  repeated report is treated, which readings are dropped, what on time means
 *  and, where the figures carry an early-departure rate, what it counts, then
 *  the method version, the day the aggregates were built, the timetables
 *  in force, where each day's observations came from and the period's
 *  completeness and clamp share. A reader comparing two agencies or two exports can open it to see
 *  they share a definition. Every value comes from the API response's
 *  `definition` field (pipeline/reports/definition.py is the source of
 *  truth); an identifier this build has no words for is shown as the API
 *  sent it, so text and definition can never silently drift apart. */
export function DefinitionMetaBlock({ definition, agencyNames, tiers }: BlockProps) {
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
      {definition.source_mix != null && definition.source_mix.length > 0 && (
        <Sources mix={definition.source_mix} agencyNames={agencyNames} />
      )}
      {definition.feed_quality != null && definition.feed_quality.length > 0 && (
        tiers != null ? (
          <FeedQualityLines
            quality={definition.feed_quality}
            agencyNames={agencyNames}
            tierOf={(id) => tiers.get(id) ?? null}
          />
        ) : (
          <CatalogueFeedQualityLines quality={definition.feed_quality} agencyNames={agencyNames} />
        )
      )}
    </details>
  );
}

/** Each agency's realtime completeness and clamp share over the period, for
 *  the whole agency, in the agencies board's words: a figure the gate flags
 *  reads as the board's unranked reason does, plainly, and one it cannot
 *  compute says why, never 0. One sentence for one agency, and a list under
 *  each agency's name for several. */
type FeedQualityProps = { quality: FeedQuality[]; agencyNames?: ReadonlyMap<number, string> };

function CatalogueFeedQualityLines(props: FeedQualityProps) {
  const tierOf = useCapabilityTierOf();
  return <FeedQualityLines {...props} tierOf={tierOf} />;
}

function FeedQualityLines({
  quality,
  agencyNames,
  tierOf,
}: FeedQualityProps & { tierOf: (agencyId: number) => TierState }) {
  const { t } = useTranslation();
  if (quality.length === 1) return <p>{t("definitionMeta.feed_quality", qualityParts(quality[0], t, tierOf(quality[0].agency_id)))}</p>;
  return (
    <>
      <p>{t("definitionMeta.feed_quality_heading")}</p>
      <ul>
        {quality.map((own) => (
          <li key={own.agency_id}>
            {t("definitionMeta.feed_quality_agency", {
              agency: agencyNames?.get(own.agency_id) ?? String(own.agency_id),
              ...qualityParts(own, t, tierOf(own.agency_id)),
            })}
          </li>
        ))}
      </ul>
    </>
  );
}

/** One agency's coverage and clamp share as the feed-quality sentences
 *  interpolate them. */
function qualityParts(own: FeedQuality, t: TFunction, tier: TierState): { coverage: string; clamp: string } {
  return { coverage: coverageWords(own, t, tier), clamp: clampWords(own, t) };
}

/** A basic-tier feed's unmatched coverage is outside its tier, and while
 *  the tier loads no cause is given rather than one the tier may replace. */
function coverageWords(own: FeedQuality, t: TFunction, tier: TierState): string {
  if (own.coverage_pct == null) {
    const missing = own.coverage_missing ?? "no_data";
    if (missing === "unmatched" && tier === "basic") {
      return `${t("network.col_observed_coverage")} ${t("capability.basic.figure")}`;
    }
    if (missing === "unmatched" && tier === "loading") return t("network.observed_coverage_missing");
    const reason =
      missing === "no_data"
        ? t("definitionMeta.feed_quality_no_data")
        : t(`network.coverage_unknown_why.${missing}`, { defaultValue: missing });
    return t("definitionMeta.feed_quality_coverage_missing", { reason });
  }
  if (own.coverage_low) {
    return t("network.unranked_reason.coverage_low", {
      pct: fixedTenth(own.coverage_pct),
      min: formatNumber(own.min_coverage_pct),
    });
  }
  if (own.observed_trips == null || own.coverage_planned_trips == null) return t("network.observed_coverage_missing");
  return t("network.observed_coverage", {
    pct: fixedTenth(own.coverage_pct),
    observed: formatNumber(own.observed_trips),
    planned: formatNumber(own.coverage_planned_trips),
  });
}

function clampWords(own: FeedQuality, t: TFunction): string {
  if (own.clamp_pct == null) return t("definitionMeta.feed_quality_clamp_missing");
  if (own.clamp_high) {
    return t("network.unranked_reason.clamp_high", {
      pct: fixedTenth(own.clamp_pct),
      max: formatNumber(own.max_clamp_pct),
    });
  }
  return t("definitionMeta.feed_quality_clamp", { pct: fixedTenth(own.clamp_pct) });
}

/** The days each ingest source supplied the observations on: one sentence for
 *  one agency, and a list under each agency's name for several. Counted in
 *  days, not observations, because a live day holds far fewer of them; what
 *  the two sources are follows whenever either supplied a day. */
function Sources({ mix, agencyNames }: { mix: SourceMix[]; agencyNames?: ReadonlyMap<number, string> }) {
  const { t } = useTranslation();
  const explained = mix.some((own) => own.archive_days > 0 || own.live_days > 0);
  return (
    <>
      {mix.length === 1 ? (
        <p>{t("definitionMeta.observations", sourceParts(mix[0], t))}</p>
      ) : (
        <>
          <p>{t("definitionMeta.observations_heading")}</p>
          <ul>
            {mix.map((own) => (
              <li key={own.agency_id}>
                {t("definitionMeta.observations_agency", {
                  agency: agencyNames?.get(own.agency_id) ?? String(own.agency_id),
                  ...sourceParts(own, t),
                })}
              </li>
            ))}
          </ul>
        </>
      )}
      {explained && <p>{t("definitionMeta.observations_explained")}</p>}
    </>
  );
}

/** One agency's sources as the `parts` and the range's day `count` the
 *  sentences interpolate, leaving out a source that supplied no day. */
function sourceParts(own: SourceMix, t: TFunction): { parts: string; count: number } {
  const parts = [
    own.archive_days > 0 ? t("definitionMeta.observations_archive", { count: own.archive_days }) : null,
    own.live_days > 0 ? t("definitionMeta.observations_live", { count: own.live_days }) : null,
    own.none_days > 0 ? t("definitionMeta.observations_unrecorded", { count: own.none_days }) : null,
  ].filter((part) => part != null);
  const count = own.archive_days + own.live_days + own.none_days;
  return { parts: parts.join(t("definitionMeta.observations_separator")), count };
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
 *  fetched where its name carries one, else by its name, and by its content's
 *  short hash too where the archive maps it. */
function timetableParts(versions: TimetableVersion[], t: TFunction): { versions: string; count: number } {
  const parts = versions.map((version) => {
    if (version.name == null) return t("definitionMeta.timetable_unrecorded", { count: version.days });
    const hash = version.content_hash;
    if (version.fetched_on != null) {
      const day = formatDate(version.fetched_on);
      return hash != null
        ? t("definitionMeta.timetable_fetched_hashed", { count: version.days, day, hash })
        : t("definitionMeta.timetable_fetched", { count: version.days, day });
    }
    return hash != null
      ? t("definitionMeta.timetable_named_hashed", { count: version.days, name: version.name, hash })
      : t("definitionMeta.timetable_named", { count: version.days, name: version.name });
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
 *  the sentence says outright: a reader otherwise assumes a window. A named
 *  preset is called by its reader's name, in the words the printed footnotes
 *  use (pipeline/reports/definition.py), so a figure compared with a
 *  published one says which convention it follows. */
function onTimeSentence(definition: DefinitionMeta, t: TFunction): string | null {
  if (definition.late_tolerance_sec == null) return null;
  const late = formatDuration(definition.late_tolerance_sec);
  const preset = definition.preset != null && PRESET_NAMES.has(definition.preset) ? definition.preset : null;
  const named = preset == null ? "" : "_named";
  const values = { late, preset: preset == null ? "" : t(`definitionMeta.preset.${preset}`) };
  return definition.early_tolerance_sec == null
    ? t(`definitionMeta.on_time_late${named}`, values)
    : t(`definitionMeta.on_time_late_early${named}`, { ...values, early: formatDuration(definition.early_tolerance_sec) });
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
