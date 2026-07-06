import { useEffect, useState } from "react";
import { applyTheme, readThemePref, writeThemePref, type Theme } from "../styles/theme";

/** Current theme + a setter that persists to localStorage and updates
 *  data-theme on <html>. Applies the initial theme on mount too — redundant
 *  with index.html's pre-mount script in the common case, but keeps this
 *  hook correct standalone (e.g. under test, where the inline script never ran). */
export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(readThemePref);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  function setTheme(next: Theme): void {
    writeThemePref(next);
    setThemeState(next);
  }

  return [theme, setTheme];
}
