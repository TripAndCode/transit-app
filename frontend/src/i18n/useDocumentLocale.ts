import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { FILTER_SEPARATOR } from "../utils/format";

/** Keeps the page's <title> and <html lang> on the active language.
 *  index.html sets both before first paint; this follows a later switch, so
 *  a screen reader keeps reading the page in the language it is shown in.
 *  `titleParts` (the screen, then its agency) lead the title, so a tab or a
 *  history entry says which screen it is; missing parts are left out. */
export function useDocumentLocale(titleParts: readonly (string | null | undefined)[] = []): void {
  const { t, i18n } = useTranslation();
  const language = i18n.resolvedLanguage ?? i18n.language;
  const title = [...titleParts, t("header.app_title")].filter(Boolean).join(FILTER_SEPARATOR);
  useEffect(() => {
    document.title = title;
    document.documentElement.lang = language;
  }, [title, language]);
}
