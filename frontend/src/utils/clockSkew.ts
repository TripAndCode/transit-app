/** How far ahead of the client clock a server timestamp may read (as a
 *  negative age, in ms) and still count as current rather than future-dated.
 *  A client clock a few seconds behind the server's makes a just-captured
 *  report look slightly ahead of "now". */
export const CLOCK_SKEW_ALLOWANCE_MS = -60_000;
