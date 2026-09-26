import { useEffect, useState } from "react";
import { applyTheme, readThemePref, subscribeSystemTheme, writeThemePref, type Theme } from "./theme";

/** Current theme preference + a setter that persists it and repaints
 *  data-theme on <html>. Applies on mount too — redundant with index.html's
 *  pre-mount script in the common case, but keeps this hook correct standalone
 *  (e.g. under test, where the inline script never ran).
 *
 *  While the preference is `"system"` the hook also tracks live OS appearance
 *  changes; choosing light or dark detaches that listener, so an explicit
 *  choice is never overwritten by the OS. */
export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(readThemePref);

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    return subscribeSystemTheme(() => applyTheme("system"));
  }, [theme]);

  function setTheme(next: Theme): void {
    writeThemePref(next);
    setThemeState(next);
  }

  return [theme, setTheme];
}
