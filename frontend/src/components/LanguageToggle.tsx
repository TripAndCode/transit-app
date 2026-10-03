import { useState } from "react";
import { useTranslation } from "react-i18next";
import { changeLocale, type Locale } from "../i18n";

/** Each language named in itself, so a reader who can't read the current one
 *  still finds theirs. */
const LANGUAGE_NAMES: Record<Locale, string> = { en: "English", ja: "日本語" }; // i18n-ignore: each language names itself
const ORDER: readonly Locale[] = ["en", "ja"];

/** The language switch for pages outside the app shell, where the account
 *  menu's switch is not on screen. */
export function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const current = (i18n.resolvedLanguage ?? "ja") as Locale;

  // The other language may still have to be fetched; clicks while a switch is
  // in flight would only queue more switches.
  async function choose(lng: Locale) {
    if (lng === current || pending) return;
    setPending(true);
    const switched = await changeLocale(i18n, lng).finally(() => setPending(false));
    setFailed(!switched);
  }

  return (
    <div className="language-toggle">
      <div role="group" aria-label={t("common.language_aria")} className="language-toggle__options">
        {ORDER.map((lng) => (
          <button key={lng} type="button" lang={lng} aria-pressed={lng === current} onClick={() => void choose(lng)}>
            {LANGUAGE_NAMES[lng]}
          </button>
        ))}
      </div>
      {failed && (
        <p role="status" className="language-toggle__error">
          {t("common.language_switch_error")}
        </p>
      )}
    </div>
  );
}
