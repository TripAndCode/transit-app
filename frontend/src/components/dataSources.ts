import type { Locale } from "../i18n";

/** Where feeds published under one credit are fetched from: every URL on
 *  `host` whose path starts with `pathPrefix`, or any path when it is unset. */
type FeedLocation = { host: string; pathPrefix?: string };

/** One open-data source the app's figures are derived from, as its credit
 *  shows it. `DataSourceCredits` renders one credit per entry, the terms
 *  repeat each credit verbatim, and every seeded agency's feed must belong to
 *  an entry; their tests read this list, so a source cannot count as credited
 *  without a credit being shown. */
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

export const DATA_SOURCES: readonly DataSource[] = [
  // Aomori City Bus registers its GTFS and GTFS-RT on ckan.odpt.org under
  // CC BY 4.0, which requires naming the provider, linking the material and
  // the licence, and saying the data was modified; the credit follows ODPT's
  // own template for modified CC BY material. The operator's own site serves
  // both feeds; the collector polls ODPT's key-less mirror of the realtime one,
  // because the operator's site drops the collector's address. That mirror
  // serves operators nationwide, so only this dataset's path on it is Aomori's.
  {
    creditKey: "credits.aomori",
    feeds: [
      { host: "aomoricitybus.com" },
      { host: "api-public.odpt.org", pathPrefix: "/api/v4/gtfs/realtime/odpt_AomoriCity_AllLines" },
    ],
    prefectureKey: "prefectures.aomori",
    links: (locale) => ({
      static: "https://ckan.odpt.org/dataset/aomori_city_all_lines",
      realtime: "https://ckan.odpt.org/dataset/odpt_aomori_city_all_lines",
      license: `https://creativecommons.org/licenses/by/4.0/deed.${locale}`,
    }),
  },
  // The Hiroshima Bus Association publishes its operators' feeds under CC0,
  // which requires no credit; they are credited anyway. Its site asks visitors
  // to link only its top page, which links on to the GTFS page.
  {
    creditKey: "credits.hiroshima",
    feeds: [{ host: "ajt-mobusta-gtfs.mcapps.jp" }],
    prefectureKey: "prefectures.hiroshima",
    links: (locale) => ({
      site: "https://www.bus-kyo.or.jp/",
      license: `https://creativecommons.org/publicdomain/zero/1.0/deed.${locale}`,
    }),
  },
];

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
