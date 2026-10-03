/**
 * Pure scoring/filtering for the command palette's search box. No fuzzy
 * library dependency: a prefix match ranks highest, a plain substring next,
 * and an in-order (but not contiguous) subsequence match last — enough to
 * find "42" in "42 金沢港〜夕日寺" or "reports" in "分析" via a `sublabel`
 * of "analysis/ranking", without pulling in a scoring library for a single
 * search box.
 */

export type Searchable = {
  label: string;
  sublabel?: string;
};

/**
 * Score `target` against `query` (already trimmed, expected non-empty), or
 * `null` if `query` is not a subsequence of `target` at all. Higher is
 * better; a shorter target wins ties within the same match tier so a
 * precise, short label outranks a long one that merely contains the query.
 */
export function scoreMatch(query: string, target: string): number | null {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (t.startsWith(q)) return 300 - t.length;
  const idx = t.indexOf(q);
  if (idx >= 0) return 200 - idx - t.length * 0.01;

  let qi = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) qi++;
  }
  if (qi < q.length) return null;
  return 100 - t.length;
}

/** Best score for an item across its label and optional sublabel. */
function bestScore(item: Searchable, query: string): number | null {
  const labelScore = scoreMatch(query, item.label);
  const subScore = item.sublabel ? scoreMatch(query, item.sublabel) : null;
  if (labelScore == null) return subScore;
  if (subScore == null) return labelScore;
  return Math.max(labelScore, subScore);
}

/**
 * Filter and rank `items` by `query`. An empty (or whitespace-only) query
 * returns `items` unchanged, in their original order — the palette's
 * "browse everything" view before the user types anything.
 */
export function filterItems<T extends Searchable>(items: readonly T[], query: string): T[] {
  const q = query.trim();
  if (!q) return [...items];
  const scored: { item: T; score: number }[] = [];
  for (const item of items) {
    const score = bestScore(item, q);
    if (score != null) scored.push({ item, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.map((s) => s.item);
}
