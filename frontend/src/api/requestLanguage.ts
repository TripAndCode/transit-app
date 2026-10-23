import type { i18n as I18n } from "i18next";

/** The language a request asks the server to write its text in. client.ts
 *  sends it as Accept-Language, and every cached query whose response holds
 *  such text keys on it, so the two cannot disagree. "ja" matches the
 *  server's LocaleMiddleware default. Pass the instance `useTranslation()`
 *  returns when reading it during render: that one changes on a language
 *  switch and re-renders the caller. */
export function requestLanguage(instance: Pick<I18n, "resolvedLanguage" | "language">): string {
  return instance.resolvedLanguage ?? instance.language ?? "ja";
}
