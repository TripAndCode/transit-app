/** Whether point `i` of an x axis with `count` points gets a label: about
 *  `target` spread evenly, plus the last. An evenly spread one too close to
 *  the last is left out, since the two would overlap. */
export function showsLabel(i: number, count: number, target = 7): boolean {
  const last = count - 1;
  if (i === last) return true;
  const step = Math.max(1, Math.ceil(count / target));
  return i % step === 0 && last - i >= step;
}
