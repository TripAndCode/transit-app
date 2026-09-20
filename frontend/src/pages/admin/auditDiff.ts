type DiffEntry = {
  key: string;
  before: unknown;
  after: unknown;
  changed: boolean;
};

/** One diff-pill entry per key present in either snapshot, sorted for a
 * stable render order. `null` for both means the audit row carries no
 * diff data (e.g. a merged `login_events` row) and yields no entries. */
export function diffEntries(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
): DiffEntry[] {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  return [...keys].sort().map((key) => {
    const b = before?.[key] ?? null;
    const a = after?.[key] ?? null;
    return { key, before: b, after: a, changed: JSON.stringify(b) !== JSON.stringify(a) };
  });
}

/** Human-readable rendering of one diff-pill value. */
export function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}
