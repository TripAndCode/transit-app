import i18n, { type BackendModule, type i18n as I18n, type ResourceKey } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import { design } from "./design";

export const SUPPORTED_LOCALES = ["ja", "en"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

type TranslationLoaders = Record<Locale, () => Promise<{ default: ResourceKey }>>;

// Each language's strings are a separate chunk, so the entry carries none of
// them and only the active language is fetched before first render.
// index.html's pre-mount script preloads that chunk alongside the entry.
const translationLoaders: TranslationLoaders = {
  ja: () => import("./locales/ja.json"),
  en: () => import("./locales/en.json"),
};

function translationBackend(loaders: TranslationLoaders): BackendModule {
  return {
    type: "backend",
    init() {},
    read(language, namespace, callback) {
      const load = namespace === "translation" ? loaders[language as Locale] : undefined;
      if (!load) {
        callback(new Error(`no "${namespace}" bundle for "${language}"`), false);
        return;
      }
      load().then(
        (module) => callback(null, module.default),
        (error: unknown) => callback(error instanceof Error ? error : String(error), false),
      );
    },
  };
}

/** Resolves once the detected language's strings are loaded. */
export function initI18n(instance: I18n, loaders: TranslationLoaders = translationLoaders) {
  return instance
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
      detection: {
        order: ["localStorage", "navigator"],
        lookupLocalStorage: "app.locale",
        caches: ["localStorage"],
      },
      interpolation: { escapeValue: false },
      returnNull: false,
    });
}

/** Switches the UI language once its strings have loaded. i18next would
 *  switch even when the fetch fails and render bare keys, so a failed fetch
 *  leaves the current language in place instead. */
export async function changeLocale(instance: I18n, lng: Locale): Promise<void> {
  await instance.loadLanguages(lng);
  if (instance.hasResourceBundle(lng, "translation")) await instance.changeLanguage(lng);
}

export const i18nReady = initI18n(i18n.use(initReactI18next));

export default i18n;
