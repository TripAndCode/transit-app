import type { AuditSnapshot } from "../../api/admin";

type DiffEntry = {
  key: string;
  before: unknown;
  after: unknown;
  changed: boolean;
};

function asMap(snapshot: AuditSnapshot): Record<string, unknown> {
  if (snapshot == null) return {};
  // A row list is keyed by position: the rows have no stable identity the
  // audit row carries, and position is what makes two snapshots comparable.
  if (Array.isArray(snapshot)) return Object.fromEntries(snapshot.map((row, i) => [String(i), row]));
  return snapshot;
}

/** One diff-pill entry per key present in either snapshot, sorted for a
 * stable render order. `null` for both means the audit row carries no
 * diff data (e.g. a merged `login_events` row) and yields no entries. */
export function diffEntries(before: AuditSnapshot, after: AuditSnapshot): DiffEntry[] {
  const b = asMap(before);
  const a = asMap(after);
  const keys = new Set([...Object.keys(b), ...Object.keys(a)]);
  return [...keys].sort().map((key) => {
    const bv = b[key] ?? null;
    const av = a[key] ?? null;
    return { key, before: bv, after: av, changed: JSON.stringify(bv) !== JSON.stringify(av) };
  });
}

/** Human-readable rendering of one diff-pill value. */
export function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  // Objects reach here whenever a payload is a row list; `String()` on one
  // renders "[object Object]", which tells an operator nothing.
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
