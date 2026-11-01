import type { TFunction } from "i18next";
import type { Locale } from "../i18n";
import SOURCES from "./dataSources.json";

/** Where feeds published under one credit are fetched from: every URL on
 *  `host` whose path starts with `pathPrefix`, or any path when it is unset. */
type FeedLocation = { host: string; pathPrefix?: string };

/** One open-data source the app's figures are derived from, as its credit
 *  shows it. `DataSourceCredits` renders one credit per entry, the terms
 *  repeat each credit verbatim, and every seeded agency's feed must belong to
 *  an entry; their tests read this list, so a source cannot count as credited
 *  without a credit being shown. The list itself is `dataSources.json`, which
 *  `pipeline/data_sources.py` reads too, so the CSVs the server writes credit
 *  the same sources in the same words as the pages. */
export type DataSource = {
  /** The credit sentence; each `<tag>` in it wraps the link `links(locale)[tag]`. */
  creditKey: "credits.aomori" | "credits.hiroshima";
  /** Where agencies' GTFS and GTFS-RT feeds are fetched from under this credit. */
  feeds: readonly FeedLocation[];
  /** The prefecture every agency this source publishes runs in, which the
   *  agency picker groups by. The feeds carry no location of their own, so a
   *  source whose agencies span prefectures cannot name one here. */
  prefectureKey: "prefectures.aomori" | "prefectures.hiroshima";
  links: (locale: Locale) => Record<string, string>;
};

// Why each entry of dataSources.json reads as it does:
// - credits.aomori: Aomori City Bus registers its GTFS and GTFS-RT on
//   ckan.odpt.org under CC BY 4.0, which requires naming the provider, linking
//   the material and the licence, and saying the data was modified; the credit
//   follows ODPT's own template for modified CC BY material. The operator's own
//   site serves both feeds; the collector polls ODPT's key-less mirror of the
//   realtime one, because the operator's site drops the collector's address.
//   That mirror serves operators nationwide, so only this dataset's path on it
//   is Aomori's.
// - credits.hiroshima: the Hiroshima Bus Association publishes its operators'
//   feeds under CC0, which requires no credit; they are credited anyway. Its
//   site asks visitors to link only its top page, which links on to the GTFS
//   page.
// A link whose URL holds `{locale}` points at that locale's page.
export const DATA_SOURCES: readonly DataSource[] = SOURCES.map(({ creditKey, feeds, prefectureKey, links }) => ({
  creditKey: creditKey as DataSource["creditKey"],
  feeds,
  prefectureKey: prefectureKey as DataSource["prefectureKey"],
  links: (locale: Locale) =>
    Object.fromEntries(Object.entries(links).map(([tag, href]) => [tag, href.replace("{locale}", locale)])),
}));

/** A `<tag>name</tag>` link in a credit sentence. */
const LINKED_NAME = /<([a-z]+)>(.*?)<\/\1>/g;

/** A source's credit as plain text, each linked name followed by its URL in
 *  the `credits.link_with_url` pattern: a printed page or a CSV cell keeps no
 *  link, and CC BY asks that the material and the licence be linked.
 *  `pipeline/data_sources.py` renders the server's CSV credit the same way. */
export function creditText(source: DataSource, t: TFunction, locale: Locale): string {
  const links = source.links(locale);
  return t(source.creditKey).replace(LINKED_NAME, (_, tag: string, name: string) =>
    t("credits.link_with_url", { name, url: links[tag] }),
  );
}

/** The source a feed URL is published under, or null for a URL no source
 *  covers or that does not parse. */
export function dataSourceOf(feedUrl: string): DataSource | null {
  let url: URL;
  try {
    url = new URL(feedUrl);
  } catch {
    return null;
  }
  return (
    DATA_SOURCES.find((source) =>
      source.feeds.some(({ host, pathPrefix = "/" }) => url.hostname === host && url.pathname.startsWith(pathPrefix)),
    ) ?? null
  );
}
