import { useEffect, useSyncExternalStore } from "react";
import { applyTheme, readThemePref, subscribeSystemTheme, writeThemePref, type Theme } from "./theme";

// One preference for the whole app. Every caller reads the same value, so a
// choice made in one place (the sidebar menu) is what every other (the command
// palette) sees, and exactly one OS-appearance listener exists, attached only
// while the preference is "system".

/** The choice held for this session when storage refuses the write; null while
 *  storage is the record. */
let sessionPref: Theme | null = null;
const listeners = new Set<() => void>();
let stopFollowingSystem: (() => void) | null = null;

function currentPref(): Theme {
  return sessionPref ?? readThemePref();
}

function syncSystemListener(): void {
  const wanted = listeners.size > 0 && currentPref() === "system";
  if (wanted && !stopFollowingSystem) {
    stopFollowingSystem = subscribeSystemTheme(() => applyTheme("system"));
  } else if (!wanted && stopFollowingSystem) {
    stopFollowingSystem();
    stopFollowingSystem = null;
  }
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  syncSystemListener();
  return () => {
    listeners.delete(onChange);
    syncSystemListener();
  };
}

function setTheme(next: Theme): void {
  sessionPref = writeThemePref(next) ? null : next;
  syncSystemListener();
  for (const notify of [...listeners]) notify();
}

/** Current theme preference + a setter that persists it and repaints
 *  data-theme on <html>. Applies on mount too — redundant with index.html's
 *  pre-mount script in the common case, but keeps this hook correct standalone
 *  (e.g. under test, where the inline script never ran).
 *
 *  The preference is shared by every caller. While it is `"system"` the app
 *  also tracks live OS appearance changes; choosing light or dark detaches that
 *  listener, so an explicit choice is never overwritten by the OS. */
export function useTheme(): [Theme, (next: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, currentPref, readThemePref);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return [theme, setTheme];
}
