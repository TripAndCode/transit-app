import i18n, { type BackendModule, type i18n as I18n, type ResourceKey } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import { design } from "./design";
import enJsonUrl from "./locales/en.json?url";
import jaJsonUrl from "./locales/ja.json?url";

export const SUPPORTED_LOCALES = ["ja", "en"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

/** Loads one language's strings; `attempt` counts from 0 for that language. */
type TranslationLoader = (attempt: number) => Promise<ResourceKey>;
type TranslationLoaders = Record<Locale, TranslationLoader>;

// The first attempt imports the language's chunk, which index.html's
// pre-mount script preloads alongside the entry. A browser keeps a failed
// module import failed for the rest of the page, so retrying import() never
// reaches the network again; retries fetch the same strings as JSON instead.
function translationLoader(importChunk: () => Promise<{ default: ResourceKey }>, jsonUrl: string): TranslationLoader {
  return async (attempt) => {
    if (attempt === 0) return (await importChunk()).default;
    const response = await fetch(jsonUrl);
    if (!response.ok) throw new Error(`${jsonUrl} responded ${response.status}`);
    return (await response.json()) as ResourceKey;
  };
}

// Each language's strings are a separate chunk, so the entry carries none of
// them and only the active language is fetched before first render.
const translationLoaders: TranslationLoaders = {
  ja: translationLoader(() => import("./locales/ja.json"), jaJsonUrl),
  en: translationLoader(() => import("./locales/en.json"), enJsonUrl),
};

function translationBackend(loaders: TranslationLoaders): BackendModule {
  const attempts = new Map<string, number>();
  return {
    type: "backend",
    init() {},
    read(language, namespace, callback) {
      const load = namespace === "translation" ? loaders[language as Locale] : undefined;
      if (!load) {
        callback(new Error(`no "${namespace}" bundle for "${language}"`), false);
        return;
      }
      const attempt = attempts.get(language) ?? 0;
      attempts.set(language, attempt + 1);
      // `true` alongside an error marks it retryable, the i18next backend
      // convention; its connector then retries with backoff (see initI18n).
      load(attempt).then(
        (strings) => callback(null, strings),
        (error: unknown) => callback(error instanceof Error ? error : String(error), true),
      );
    },
  };
}

/** Resolves to whether the detected language's strings loaded, after any
 *  retries. i18next itself resolves either way. */
export async function initI18n(instance: I18n, loaders: TranslationLoaders = translationLoaders): Promise<boolean> {
  await instance
    .use(translationBackend(loaders))
    .use(LanguageDetector)
    .init({
      // The design namespace is small enough to ship bundled for both
      // languages; translation bundles come from the backend above.
      resources: {
        ja: { design: design.ja },
        en: { design: design.en },
      },
      partialBundledLanguages: true,
      // No supported language falls back to another: the locale files hold
      // the same keys (linted), and a fallback language would be fetched
      // alongside the active one. `default` resolves an unsupported one.
      fallbackLng: { ja: [], en: [], default: ["ja"] },
      supportedLngs: SUPPORTED_LOCALES,
      // A failed chunk fetch is retried after 250, 500 and 1000 ms: a brief
      // network blip recovers, and a lasting failure reaches main.tsx's
      // reload notice within a couple of seconds.
      maxRetries: 3,
      retryTimeout: 250,
      // index.html's pre-mount preload repeats this resolution; the
      // "index.html locale preload" tests in localeLoading.test.tsx keep
      // the two in agreement.
      detection: {
        order: ["localStorage", "navigator"],
        lookupLocalStorage: "app.locale",
        caches: ["localStorage"],
      },
      interpolation: { escapeValue: false },
      returnNull: false,
    });
  return instance.hasResourceBundle(instance.language, "translation");
}

/** Switches the UI language once its strings have loaded, and resolves to
 *  whether they did. i18next would switch even when the fetch fails and
 *  render bare keys, so a failed fetch leaves the current language in place
 *  instead. The strings are read with `reloadResources`, not
 *  `loadLanguages`: the latter remembers a language as requested even when
 *  its load failed and never asks again, so a switch that failed once would
 *  do nothing for the rest of the page.
 *
 *  The result comes from the resource store, not `instance.language`: the
 *  i18n object `useTranslation` hands a component is a snapshot whose
 *  language fields keep their old values after the switch. */
export async function changeLocale(instance: I18n, lng: Locale): Promise<boolean> {
  if (!instance.hasResourceBundle(lng, "translation")) await instance.reloadResources(lng, "translation");
  if (!instance.hasResourceBundle(lng, "translation")) return false;
  await instance.changeLanguage(lng);
  return true;
}

export const i18nReady = initI18n(i18n.use(initReactI18next));

export default i18n;
