import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LOCALE_NAMES, type Locale } from "../i18n";
import { useLocaleSwitch } from "../i18n/useLocaleSwitch";

const ORDER: readonly Locale[] = ["en", "ja"];

/** The language switch for pages outside the app shell, where the account
 *  menu's switch is not on screen. */
export function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const [failed, setFailed] = useState(false);
  const { switchTo } = useLocaleSwitch(() => setFailed(true));
  const current = (i18n.resolvedLanguage ?? "ja") as Locale;

  function choose(lng: Locale) {
    if (lng === current) return;
    setFailed(false);
    void switchTo(lng);
  }

  return (
    <div className="language-toggle">
      <div role="group" aria-label={t("common.language_aria")} className="language-toggle__options">
        {ORDER.map((lng) => (
          <button key={lng} type="button" lang={lng} aria-pressed={lng === current} onClick={() => choose(lng)}>
            {LOCALE_NAMES[lng]}
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
