/** The app's three layout breakpoints, in pixels.
 *
 *  A CSS custom property cannot be used in a media query's feature value, so
 *  these cannot live in `global.css` next to the other design tokens: media
 *  queries are evaluated before custom properties are substituted. TypeScript
 *  is therefore the source of truth, consumed directly by anything that builds
 *  a query string in JS (`useMediaQuery`) and named in a comment beside every
 *  literal in a stylesheet so a reader can tell an intentional breakpoint from
 *  an arbitrary width.
 *
 *  sm  — phone / narrow: single-column, side rails collapse.
 *  md  — tablet / split: two-pane layouts fold to one.
 *  lg  — wide desktop: the widest layout tier.
 */
export const BP = {
  sm: 640,
  md: 900,
  lg: 1200,
} as const;
