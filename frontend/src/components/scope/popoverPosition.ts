/** Where a popover starts so it opens under its token yet ends inside its
 *  container: slid left as far as needed, never past the container's start. */
export function popoverLeft(tokenLeft: number, popoverWidth: number, containerWidth: number): number {
  return Math.max(0, Math.min(tokenLeft, containerWidth - popoverWidth));
}
