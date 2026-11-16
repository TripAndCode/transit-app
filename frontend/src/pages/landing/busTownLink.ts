/** The Bus Town CTA's target: the visitor's last-chosen agency's Bus Town, or
 *  "/" to choose one when none is stored yet. Kept as a pure function so both
 *  outcomes are unit-testable without mounting the page. */
export function busTownHref(lastAgencyId: number | null): string {
  return lastAgencyId != null ? `/agencies/${lastAgencyId}/bus-town` : "/";
}
