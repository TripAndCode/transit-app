const KEY = "transit.welcomeSeen";

/** Read whether this browser has already been routed through "/" once
 *  before. OnboardingGate sends a fresh anonymous visitor to "/welcome"
 *  exactly once and never again once this is set, regardless of whether
 *  the visitor is still anonymous or has since signed in.
 *  Returns false when unset or localStorage is unavailable. */
export function readWelcomeSeen(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
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
