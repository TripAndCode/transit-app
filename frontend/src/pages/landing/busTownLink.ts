/** The Bus Town CTA's target: a visitor who would reach the signed-in app
 *  (a session, or a guest-enabled deployment) goes straight to Bus Town for
 *  their last-chosen agency, or to "/" to choose one when none is stored
 *  yet; anyone else goes to sign-in first. Kept as a pure function so the
 *  three outcomes are unit-testable without mounting the page. */
export function busTownHref(reachesApp: boolean, lastAgencyId: number | null): string {
  if (!reachesApp) return "/login";
  return lastAgencyId != null ? `/agencies/${lastAgencyId}/bus-town` : "/";
}
