/** Order-insensitive equality for two route/pattern-code selections, used to
 *  decide whether a draft filter differs from what's applied. */
export function sameCodes(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const seen = new Set(b);
  return a.every((code) => seen.has(code));
}
