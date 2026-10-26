import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { DataSourceCredits } from "./DataSourceCredits";
import { DATA_SOURCES, type DataSource } from "./dataSources";

const AOMORI_STATIC = "https://ckan.odpt.org/dataset/aomori_city_all_lines";
const AOMORI_REALTIME = "https://ckan.odpt.org/dataset/odpt_aomori_city_all_lines";
const BUS_KYO_TOP = "https://www.bus-kyo.or.jp/";

function creditParagraphs() {
  const section = screen.getByRole("region", { name: i18n.t("credits.title") });
  return { section, paragraphs: [...section.querySelectorAll("p")].map((p) => p.textContent) };
}

describe("DataSourceCredits", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("credits each source in English: provider, data, licence, and that the data was modified", () => {
    renderWithProviders(<DataSourceCredits />);
    const { section, paragraphs } = creditParagraphs();

    expect(within(section).getByRole("heading", { name: "Data sources" })).toBeInTheDocument();
    expect(paragraphs).toEqual([
      "This service uses the following copyrighted material with modifications. Aomori City, " +
        "Aomori City Bus information and [realtime information] Aomori City Bus information, " +
        "Creative Commons License Attribution 4.0 International",
      "This service uses, with modifications, the data of each operator in Hiroshima Prefecture " +
        "that the Hiroshima Bus Association publishes on its GTFS open data site under Creative Commons CC0.",
    ]);
    const link = (name: string) => within(section).getByRole("link", { name });
    expect(link("Aomori City Bus information")).toHaveAttribute("href", AOMORI_STATIC);
    expect(link("[realtime information] Aomori City Bus information")).toHaveAttribute("href", AOMORI_REALTIME);
    expect(link("Creative Commons License Attribution 4.0 International")).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by/4.0/deed.en",
    );
    expect(link("GTFS open data site")).toHaveAttribute("href", BUS_KYO_TOP);
    expect(link("Creative Commons CC0")).toHaveAttribute(
      "href",
      "https://creativecommons.org/publicdomain/zero/1.0/deed.en",
    );
  });

  it("credits each source in Japanese, linking the Japanese licence deeds", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DataSourceCredits />);
    const { section, paragraphs } = creditParagraphs();

    expect(within(section).getByRole("heading", { name: "データの出典" })).toBeInTheDocument();
    expect(paragraphs).toEqual([
      "本サービスは、以下の著作物を改変して利用しています。青森市企業局交通部、" +
        "青森市営バス路線情報および【リアルタイム情報】青森市営バス路線情報、" +
        "クリエイティブ・コモンズ・ライセンス 表示4.0国際",
      "本サービスは、公益社団法人広島県バス協会が広島県バス協会GTFSオープンデータサイトで公開している" +
        "広島県内の各バス事業者のデータ（クリエイティブ・コモンズ CC0）を加工して利用しています。",
    ]);
    const link = (name: string) => within(section).getByRole("link", { name });
    expect(link("青森市営バス路線情報")).toHaveAttribute("href", AOMORI_STATIC);
    expect(link("【リアルタイム情報】青森市営バス路線情報")).toHaveAttribute("href", AOMORI_REALTIME);
    expect(link("クリエイティブ・コモンズ・ライセンス 表示4.0国際")).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by/4.0/deed.ja",
    );
    expect(link("広島県バス協会GTFSオープンデータサイト")).toHaveAttribute("href", BUS_KYO_TOP);
    expect(link("クリエイティブ・コモンズ CC0")).toHaveAttribute(
      "href",
      "https://creativecommons.org/publicdomain/zero/1.0/deed.ja",
    );
  });

  it("opens every source in a new tab", () => {
    renderWithProviders(<DataSourceCredits />);
    const { section } = creditParagraphs();
    for (const link of within(section).getAllByRole("link")) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer");
    }
  });

  // A licence asks for (CC BY) or invites (CC0) a credit, so an agency seeded
  // from a host that no rendered credit covers would show figures with no
  // credit for them.
  it.each(["en", "ja"] as const)("renders a credit covering the host of every seeded agency feed (%s)", async (locale) => {
    await i18n.changeLanguage(locale);
    renderWithProviders(<DataSourceCredits />);
    const rendered = [...creditParagraphs().section.querySelectorAll("p")];

    for (const host of seededFeedHosts()) {
      const source = sourceCovering(host);
      const credit = rendered.find((p) => p.textContent === i18n.t(source.creditKey).replace(/<\/?[a-z]+>/g, ""));
      expect(credit, `${host}: no rendered ${source.creditKey}`).toBeDefined();
      const hrefs = [...(credit?.querySelectorAll("a") ?? [])].map((a) => a.getAttribute("href"));
      expect(hrefs.sort(), host).toEqual(Object.values(source.links(locale)).sort());
    }
  });
});

function seededFeedHosts(): Set<string> {
  const [header, ...rows] = readFileSync(resolve(__dirname, "../../../agencies.csv"), "utf8").trim().split("\n");
  const columns = header.split(",");
  const feed = columns.indexOf("feed_url");
  const staticFeed = columns.indexOf("static_url");
  expect(feed, "agencies.csv has a feed_url column").toBeGreaterThanOrEqual(0);
  expect(staticFeed, "agencies.csv has a static_url column").toBeGreaterThanOrEqual(0);
  const hosts = new Set(
    rows.flatMap((row) => {
      const cells = row.split(",");
      return [cells[feed], cells[staticFeed]].filter(Boolean).map((url) => new URL(url).hostname);
    }),
  );
  expect(hosts.size).toBeGreaterThan(0);
  return hosts;
}

function sourceCovering(host: string): DataSource {
  const source = DATA_SOURCES.find((s) => s.feedHosts.includes(host));
  if (!source) throw new Error(`no credited data source covers ${host}`);
  return source;
}
