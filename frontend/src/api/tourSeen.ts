const KEY = "transit.tourSeen";

/** "unavailable" is distinct from "unseen" so a caller can decline to act on
 *  an unreadable flag instead of treating it as a confirmed first visit --
 *  collapsing the two would show `FirstRunTour` on every mount, forever,
 *  whenever localStorage throws. */
type TourSeenState = "seen" | "unseen" | "unavailable";

/** Set the moment a write is confirmed not to have landed -- either
 *  `setItem` threw, or it silently no-op'd (e.g. quota already exhausted by
 *  other keys, while reads still succeed). Without this, a store that only
 *  fails on write would have `readTourSeen()` report "unseen" forever and
 *  show the tour on every single mount, exactly like an unreadable store
 *  would. Scoped to this JS execution context (a reload starts fresh and
 *  re-attempts real storage). */
let writeConfirmedBroken = false;

/** Read whether this browser has already finished or dismissed the
 *  first-run dashboard tour. `FirstRunTour` shows a fresh visitor's coach
 *  marks exactly once and never again once this is set. */
export function readTourSeen(): TourSeenState {
  try {
    if (localStorage.getItem(KEY) === "1") return "seen";
    return writeConfirmedBroken ? "unavailable" : "unseen";
  } catch {
    return "unavailable";
  }
}

/** Persist that this browser has finished or dismissed the tour, verifying
 *  the write actually landed by reading it back rather than trusting
 *  `setItem` not throwing. No-ops (beyond recording the failure, see
 *  `writeConfirmedBroken` above) if localStorage is unavailable. */
export function writeTourSeen(): void {
  try {
    localStorage.setItem(KEY, "1");
    if (localStorage.getItem(KEY) !== "1") writeConfirmedBroken = true;
  } catch {
    writeConfirmedBroken = true;
  }
}

/** Test-only: clears the in-memory write-failure memory above so cases in
 *  the same test file don't leak state into each other via module scope. */
export function resetTourSeenMemoryForTests(): void {
  writeConfirmedBroken = false;
}
