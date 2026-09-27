import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import i18next from "i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { I18nextProvider, useTranslation } from "react-i18next";
import en from "./locales/en.json";
import ja from "./locales/ja.json";
import { changeLocale, initI18n } from ".";

// The suite's shared instance preloads every locale (see test/setup.ts), so
// each case here builds its own instance to observe what loads, and when.
const KEY = "common.language_aria";

function stubNavigatorLanguages(languages: readonly string[]) {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(languages);
  vi.spyOn(navigator, "language", "get").mockReturnValue(languages[0] ?? "");
}

async function initFresh(loaders?: Parameters<typeof initI18n>[1]) {
  const instance = i18next.createInstance();
  await initI18n(instance, loaders);
  return instance;
}

function LanguageLabel() {
  const { t } = useTranslation();
  return <span data-testid="label">{t(KEY)}</span>;
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("locale loading", () => {
  it("fetches only the active language before init resolves", async () => {
    localStorage.setItem("app.locale", "ja");
    const instance = await initFresh();
    expect(instance.language).toBe("ja");
    expect(instance.t(KEY)).toBe(ja.common.language_aria);
    expect(instance.hasResourceBundle("en", "translation")).toBe(false);
  });

  it.each([
    { navigatorLanguages: ["en-US"], expected: "en" },
    { navigatorLanguages: ["ja-JP", "en-US"], expected: "ja" },
    { navigatorLanguages: ["fr-FR"], expected: "ja" },
    { navigatorLanguages: ["fr-FR", "en-GB"], expected: "en" },
  ])("resolves $navigatorLanguages to $expected without fetching the other language", async ({ navigatorLanguages, expected }) => {
    stubNavigatorLanguages(navigatorLanguages);
    const instance = await initFresh();
    expect(instance.language).toBe(expected);
    const other = expected === "ja" ? "en" : "ja";
    expect(instance.hasResourceBundle(expected, "translation")).toBe(true);
    expect(instance.hasResourceBundle(other, "translation")).toBe(false);
  });

  it("keeps the current language on screen until the other one has loaded", async () => {
    localStorage.setItem("app.locale", "ja");
    const instance = await initFresh();
    const shownWhenSwitched: string[] = [];
    instance.on("languageChanged", () => shownWhenSwitched.push(instance.t(KEY)));

    const switching = changeLocale(instance, "en");
    expect(instance.language).toBe("ja");
    expect(instance.t(KEY)).toBe(ja.common.language_aria);
    await switching;

    expect(instance.language).toBe("en");
    expect(shownWhenSwitched).toEqual([en.common.language_aria]);
    expect(localStorage.getItem("app.locale")).toBe("en");
  });

  it("stays on the current language when the other can't be fetched", async () => {
    localStorage.setItem("app.locale", "ja");
    const instance = await initFresh({
      ja: () => import("./locales/ja.json"),
      en: () => Promise.reject(new Error("network down")),
    });
    await changeLocale(instance, "en");
    expect(instance.language).toBe("ja");
    expect(instance.t(KEY)).toBe(ja.common.language_aria);
  });

  it("renders the active language's strings first and the other's after switching", async () => {
    localStorage.setItem("app.locale", "ja");
    const instance = await initFresh();
    render(
      <I18nextProvider i18n={instance}>
        <LanguageLabel />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("label").textContent).toBe(ja.common.language_aria);

    await act(() => changeLocale(instance, "en"));
    expect(screen.getByTestId("label").textContent).toBe(en.common.language_aria);
  });
});

describe("index.html locale preload", () => {
  // The pre-mount script starts downloading the active language's chunk
  // alongside the entry. It has to pick the language i18next will resolve;
  // a mismatch downloads the wrong strings and fetches the right ones late.
  const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
  const PLACEHOLDER = "/*__LOCALE_CHUNKS__*/ {}";
  const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map((m) => m[1])
    .find((body) => body.includes(PLACEHOLDER));
  const CHUNKS = { ja: "/assets/ja-hash.js", en: "/assets/en-hash.js" };

  function runPreload(chunks: Record<string, string> | null) {
    const body = chunks ? script!.replace(PLACEHOLDER, JSON.stringify(chunks)) : script!;
    new Function(body)();
    return [...document.head.querySelectorAll<HTMLLinkElement>('link[rel="modulepreload"]')];
  }

  afterEach(() => {
    for (const link of document.head.querySelectorAll('link[rel="modulepreload"]')) link.remove();
  });

  it("carries the placeholder the build fills with chunk URLs", () => {
    expect(script).toBeDefined();
    expect(html.split(PLACEHOLDER)).toHaveLength(2);
  });

  it.each([
    { stored: "en", navigatorLanguages: ["ja-JP"] },
    { stored: "ja", navigatorLanguages: ["en-US"] },
    { stored: "xx", navigatorLanguages: ["en-US"] },
    { stored: null, navigatorLanguages: ["en-US"] },
    { stored: null, navigatorLanguages: ["ja-JP", "en-US"] },
    { stored: null, navigatorLanguages: ["en-US", "ja"] },
    { stored: null, navigatorLanguages: ["fr-FR"] },
    { stored: null, navigatorLanguages: ["fr-FR", "en-GB"] },
    { stored: null, navigatorLanguages: [] },
  ])("preloads the language i18next resolves (stored $stored, navigator $navigatorLanguages)", async ({ stored, navigatorLanguages }) => {
    if (stored) localStorage.setItem("app.locale", stored);
    stubNavigatorLanguages(navigatorLanguages);
    const links = runPreload(CHUNKS);
    const resolved = (await initFresh()).language as keyof typeof CHUNKS;
    expect(links.map((l) => l.getAttribute("href"))).toEqual([CHUNKS[resolved]]);
    // Same credentials mode as the entry's own module scripts, so the
    // i18n loader's import() reuses this fetch instead of repeating it.
    expect(links[0]!.getAttribute("crossorigin")).toBe("");
  });

  it("does nothing when the map is unfilled, as under the dev server", () => {
    expect(runPreload(null)).toEqual([]);
  });
});
