/** One-shot reduced-motion check for non-React call sites (a plain function,
 *  camera choreography, an effect that runs once at mount) where the value
 *  is read once rather than tracked. A component that needs to re-render on
 *  a live change should use the `useMediaQuery` hook instead. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
