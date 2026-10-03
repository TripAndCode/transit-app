import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/** Keeps the page's <title> and <html lang> on the active language.
 *  index.html sets both before first paint; this follows a later switch, so
 *  a screen reader keeps reading the page in the language it is shown in. */
export function useDocumentLocale(): void {
  const { t, i18n } = useTranslation();
  const language = i18n.resolvedLanguage ?? i18n.language;
  useEffect(() => {
    document.title = t("header.app_title");
    document.documentElement.lang = language;
  }, [t, language]);
}
