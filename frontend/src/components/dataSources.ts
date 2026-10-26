import type { Locale } from "../i18n";

/** One open-data source the app's figures are derived from, as its credit
 *  shows it. `DataSourceCredits` renders one credit per entry, the terms
 *  repeat each credit verbatim, and every seeded agency's feed host must
 *  belong to an entry; their tests read this list, so a source cannot count
 *  as credited without a credit being shown. */
export type DataSource = {
  /** The credit sentence; each `<tag>` in it wraps the link `links(locale)[tag]`. */
  creditKey: "credits.aomori" | "credits.hiroshima";
  /** Hosts that agencies' GTFS and GTFS-RT feeds are fetched from under this credit. */
  feedHosts: readonly string[];
  links: (locale: Locale) => Record<string, string>;
};

export const DATA_SOURCES: readonly DataSource[] = [
  // Aomori City Bus registers its GTFS and GTFS-RT on ckan.odpt.org under
  // CC BY 4.0, which requires naming the provider, linking the material and
  // the licence, and saying the data was modified; the credit follows ODPT's
  // own template for modified CC BY material. The operator's own site serves
  // the static feed; ODPT's key-less mirror serves the realtime one.
  {
    creditKey: "credits.aomori",
    feedHosts: ["aomoricitybus.com", "api-public.odpt.org"],
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
    feedHosts: ["ajt-mobusta-gtfs.mcapps.jp"],
    links: (locale) => ({
      site: "https://www.bus-kyo.or.jp/",
      license: `https://creativecommons.org/publicdomain/zero/1.0/deed.${locale}`,
    }),
  },
];
