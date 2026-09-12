const KEY = "transit.welcomeSeen";

/** "unavailable" is distinct from "unseen" so a caller can decline to act on
 *  an unreadable flag instead of treating it as a confirmed first visit —
 *  collapsing the two would redirect the same browser to "/welcome" on every
 *  mount, forever, whenever localStorage throws. */
type WelcomeSeenState = "seen" | "unseen" | "unavailable";

/** Set the moment a write is confirmed not to have landed — either
 *  `setItem` threw, or it silently no-op'd (e.g. quota already exhausted by
 *  other keys, while reads still succeed). Without this, a store that only
 *  fails on write would have `readWelcomeSeen()` report "unseen" forever and
 *  redirect on every single mount, exactly like an unreadable store would.
 *  Scoped to this JS execution context (a reload starts fresh and
 *  re-attempts real storage), which is all it needs to be: it only has to
 *  survive across mounts within one tab session, e.g. the redirect to
 *  "/welcome" followed by "Continue as a guest" landing back on "/". */
let writeConfirmedBroken = false;

/** Read whether this browser has already been routed through "/" once
 *  before. OnboardingGate sends a fresh anonymous visitor to "/welcome"
 *  exactly once and never again once this is set, regardless of whether
 *  the visitor is still anonymous or has since signed in. */
export function readWelcomeSeen(): WelcomeSeenState {
  try {
    if (localStorage.getItem(KEY) === "1") return "seen";
    return writeConfirmedBroken ? "unavailable" : "unseen";
  } catch {
    return "unavailable";
  }
}

/** Persist that this browser has passed the welcome step, verifying the
 *  write actually landed by reading it back rather than trusting `setItem`
 *  not throwing. No-ops (beyond recording the failure, see
 *  `writeConfirmedBroken` above) if localStorage is unavailable. */
export function writeWelcomeSeen(): void {
  try {
    localStorage.setItem(KEY, "1");
    if (localStorage.getItem(KEY) !== "1") writeConfirmedBroken = true;
  } catch {
    writeConfirmedBroken = true;
  }
}

/** Test-only: clears the in-memory write-failure memory above so cases in
 *  the same test file don't leak state into each other via module scope. */
export function resetWelcomeSeenMemoryForTests(): void {
  writeConfirmedBroken = false;
}
