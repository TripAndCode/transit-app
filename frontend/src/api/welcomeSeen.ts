const KEY = "transit.welcomeSeen";

/** "unavailable" is distinct from "unseen" so a caller can decline to act on
 *  an unreadable flag instead of treating it as a confirmed first visit —
 *  collapsing the two would redirect the same browser to "/welcome" on every
 *  mount, forever, whenever localStorage throws. */
export type WelcomeSeenState = "seen" | "unseen" | "unavailable";

/** Read whether this browser has already been routed through "/" once
 *  before. OnboardingGate sends a fresh anonymous visitor to "/welcome"
 *  exactly once and never again once this is set, regardless of whether
 *  the visitor is still anonymous or has since signed in. */
export function readWelcomeSeen(): WelcomeSeenState {
  try {
    return localStorage.getItem(KEY) === "1" ? "seen" : "unseen";
  } catch {
    return "unavailable";
  }
}

/** Persist that this browser has passed the welcome step. No-ops if
 *  localStorage is unavailable. */
export function writeWelcomeSeen(): void {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    /* ignore */
  }
}
