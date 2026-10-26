import type { i18n as I18n } from "i18next";
import { requestLanguage } from "../api/requestLanguage";
import type { Locale } from ".";

/** The locale of a static document or external page to show: English for any
 *  `en*` language, Japanese otherwise. It reads the same fallback chain as the
 *  API's Accept-Language header (`requestLanguage`), so a page and the data it
 *  fetches never pick different languages. */
export function contentLocale(instance: Pick<I18n, "resolvedLanguage" | "language">): Locale {
  return requestLanguage(instance).startsWith("en") ? "en" : "ja";
}
