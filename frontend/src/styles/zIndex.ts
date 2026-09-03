/**
 * Shared stacking-order constants for the app's fixed-position drawer
 * overlays (Sidebar's mobile nav drawer and ThreadSidebar's mobile
 * conversation-list drawer). Both drawers use an identical
 * backdrop/panel z-index pair; previously each component hardcoded its own
 * `300`/`301` literals independently, which happened to match by
 * coincidence rather than by a shared, coordinated decision about stacking
 * order between the two. Add new entries here (rather than a fresh literal)
 * for any future overlay that needs to be ordered relative to these.
 */
export const Z_INDEX = {
  drawerBackdrop: 300,
  drawer: 301,
} as const;
