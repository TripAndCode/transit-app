import { useState } from "react";
import { useTranslation } from "react-i18next";
import { changeLocale, type Locale } from ".";

/** Switches the UI language one switch at a time. The other language may
 *  still have to be fetched, so a switch can take a moment, and clicks while
 *  one is in flight would only queue more switches. A failed fetch leaves the
 *  current language in place, which alone would read as an ignored click, so
 *  `onFailure` lets each caller say so in its own way. */
export function useLocaleSwitch(onFailure: () => void): { pending: boolean; switchTo: (lng: Locale) => Promise<void> } {
  const { i18n } = useTranslation();
  const [pending, setPending] = useState(false);
  async function switchTo(lng: Locale) {
    if (pending) return;
    setPending(true);
    const switched = await changeLocale(i18n, lng).finally(() => setPending(false));
    if (!switched) onFailure();
  }
  return { pending, switchTo };
}
