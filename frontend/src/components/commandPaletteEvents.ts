/**
 * The window event CommandPalette.tsx listens for to open itself from
 * outside its own component tree (the sidebar footer hint) without either
 * side holding a reference to the other's state. Its own module — not
 * CommandPalette.tsx — so that file exports only the component itself;
 * exporting a plain function alongside a component trips
 * `react-refresh/only-export-components` (see Tooltip.tsx/tooltipPosition.ts
 * for the same split).
 */
export const COMMAND_PALETTE_OPEN_EVENT = "command-palette:open";

export function openCommandPalette(): void {
  window.dispatchEvent(new Event(COMMAND_PALETTE_OPEN_EVENT));
}
