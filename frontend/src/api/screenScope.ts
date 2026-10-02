import { useEffect, useSyncExternalStore } from "react";
import { useLocation } from "react-router-dom";
import { explicitScopeQuery } from "./scope";

/** Each screen keeps its own filters. The rail, the phone tab bar and the
 *  palette open a screen with the scope that screen last showed for the
 *  agency, never the scope of the screen being left. Remembered per browser
 *  tab (sessionStorage), so a new tab opens every screen on its defaults.
 *  Links whose job is to carry the current scope to another screen (Worth a
 *  look, "Open in Time") build their own query and are not routed here. */
const STORAGE_KEY = "transit.screenScope";
const SCREEN_PATH = /^\/agencies\/([^/]+)\/([^/]+)\/?$/;

type Scopes = Readonly<Record<string, string>>;

const listeners = new Set<() => void>();
// The parsed store, and the sessionStorage text it was parsed from. When
// sessionStorage refuses a write, `scopes` still holds it for this page.
let scopes: Scopes = {};
let scopesRaw: string | null = null;

function parse(raw: string | null): Scopes {
  if (!raw) return {};
  try {
    const value: unknown = JSON.parse(raw);
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Scopes) : {};
  } catch {
    return {};
  }
}

function snapshot(): Scopes {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return scopes;
  }
  if (raw !== scopesRaw) {
    scopesRaw = raw;
    scopes = parse(raw);
  }
  return scopes;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function keyOf(agencyId: string, screen: string): string {
  return `${agencyId}/${screen}`;
}

export function rememberScreenScope(agencyId: string, screen: string, query: string): void {
  const current = snapshot();
  const key = keyOf(agencyId, screen);
  if (current[key] === query) return;
  scopes = { ...current, [key]: query };
  try {
    const raw = JSON.stringify(scopes);
    sessionStorage.setItem(STORAGE_KEY, raw);
    scopesRaw = raw;
  } catch {
    /* sessionStorage unavailable: keep the scope for this page only. */
  }
  for (const listener of listeners) listener();
}

/** The screen a path shows, when the path is that screen's own page. A
 *  route dossier sits under Routes but is not the Routes screen. */
export function screenOf(pathname: string): { agencyId: string; screen: string } | null {
  const match = SCREEN_PATH.exec(pathname);
  return match ? { agencyId: match[1], screen: match[2] } : null;
}

/** A screen's query string: the screen on show uses its live URL, every
 *  other screen what it last showed. Also records the screen on show, so
 *  whatever builds links between screens (the rail, the palette, both always
 *  mounted) keeps the record current. */
export function useScreenQuery(): (agencyId: string | number, screen: string) => string {
  const stored = useSyncExternalStore(subscribe, snapshot, snapshot);
  const { pathname, search } = useLocation();
  const here = screenOf(pathname);
  const hereAgency = here?.agencyId ?? null;
  const hereScreen = here?.screen ?? null;
  const hereQuery = explicitScopeQuery(search);
  useEffect(() => {
    if (hereAgency && hereScreen) rememberScreenScope(hereAgency, hereScreen, hereQuery);
  }, [hereAgency, hereScreen, hereQuery]);
  return (agencyId, screen) => {
    const id = String(agencyId);
    if (here && here.agencyId === id && here.screen === screen) return hereQuery;
    return stored[keyOf(id, screen)] ?? "";
  };
}

export function withQuery(path: string, query: string): string {
  return query ? `${path}?${query}` : path;
}
