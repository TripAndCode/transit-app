import type { Agency } from "../api/types";
import { DATA_SOURCES, type DataSource } from "./dataSources";

/** Days an agency's data may trail the newest agency's before the picker
 *  says it stopped. Agencies analysed on either side of midnight, or one
 *  missed nightly run, leave a gap of a day or two that closes by itself;
 *  three days means several nightly runs in a row brought nothing. */
const STOPPED_AFTER_DAYS = 3;

type PrefectureKey = DataSource["prefectureKey"];

type AgencyGroup = {
  /** Null for agencies whose feed no known source publishes. */
  prefectureKey: PrefectureKey | null;
  agencies: Agency[];
};

export function prefectureOf(agency: Agency): PrefectureKey | null {
  let host: string;
  try {
    host = new URL(agency.feed_url).hostname;
  } catch {
    return null;
  }
  return DATA_SOURCES.find((source) => source.feedHosts.includes(host))?.prefectureKey ?? null;
}

/** The latest day any agency has data for. ISO dates order as strings. */
export function newestDataDate(agencies: Agency[]): string | null {
  let newest: string | null = null;
  for (const { latest_data_date: day } of agencies) {
    if (day && (newest == null || day > newest)) newest = day;
  }
  return newest;
}

/** Whole calendar days since the epoch, read from the ISO date itself so the
 *  viewer's time zone cannot move it. */
function dayNumber(iso: string): number {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return Date.UTC(year, month - 1, day) / 86_400_000;
}

export function hasStopped(latest: string | null, newest: string | null): boolean {
  if (!latest || !newest) return false;
  return dayNumber(newest) - dayNumber(latest) >= STOPPED_AFTER_DAYS;
}

/** 0 for current data, 1 for data that stopped, 2 for none: an agency a
 *  visitor can use today comes before one they can only look back on. */
function dataRank(agency: Agency, newest: string | null): number {
  if (!agency.latest_data_date) return 2;
  return hasStopped(agency.latest_data_date, newest) ? 1 : 0;
}

/** Agency names are Japanese whatever language the page is in, so they are
 *  ordered the Japanese way in both, and a visitor who switches language
 *  finds every card where it was. */
const NAME_ORDER = new Intl.Collator("ja");

/** Agencies by prefecture, the prefecture with more agencies first and those
 *  of no known prefecture last. Within a group, current data comes before
 *  stopped data before none, each in name order. `newest` is the newest day
 *  across every agency, not only the ones given, so a search that leaves only
 *  a stopped agency still shows it as stopped. */
export function groupAgencies(agencies: Agency[], newest: string | null): AgencyGroup[] {
  const byPrefecture = new Map<PrefectureKey | null, Agency[]>();
  for (const agency of agencies) {
    const key = prefectureOf(agency);
    const members = byPrefecture.get(key);
    if (members) members.push(agency);
    else byPrefecture.set(key, [agency]);
  }
  const groups = [...byPrefecture].map(([prefectureKey, members]) => ({
    prefectureKey,
    agencies: members.sort(
      (a, b) => dataRank(a, newest) - dataRank(b, newest) || NAME_ORDER.compare(a.agency_name, b.agency_name),
    ),
  }));
  return groups.sort(
    (a, b) =>
      Number(a.prefectureKey == null) - Number(b.prefectureKey == null) || b.agencies.length - a.agencies.length,
  );
}
