/**
 * The window event the agency switcher listens for so the command palette
 * can open it without either side holding a reference to the other's state,
 * mirroring commandPaletteEvents.ts. Its own module for the same reason:
 * exporting a plain function next to a component trips
 * `react-refresh/only-export-components`.
 */
export const AGENCY_SWITCHER_OPEN_EVENT = "agency-switcher:open";

export function openAgencySwitcher(): void {
  window.dispatchEvent(new Event(AGENCY_SWITCHER_OPEN_EVENT));
}
