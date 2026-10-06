import { DESTINATIONS } from "./destinations";

const AGENCY_SCREENS: ReadonlySet<string> = new Set([...DESTINATIONS, "ask"]);

/** The i18n key naming the screen at `pathname`, for the window title; null
 *  where the path is no screen of its own (the root redirect, an unknown
 *  segment). A route's dossier is part of Routes. */
export function pageTitleKey(pathname: string): string | null {
  const agencyScreen = /^\/agencies\/[^/]+\/([^/]+)/.exec(pathname)?.[1];
  if (agencyScreen) return AGENCY_SCREENS.has(agencyScreen) ? `nav.${agencyScreen}` : null;
  if (pathname === "/me") return "account.title";
  if (pathname === "/help") return "nav.help";
  if (pathname === "/admin" || pathname.startsWith("/admin/")) return "account.admin_link";
  return null;
}
