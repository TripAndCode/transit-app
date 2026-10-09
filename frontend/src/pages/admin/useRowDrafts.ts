import { useState } from "react";

type Entry<Row, Draft> = {
  draft: Draft;
  /** The row this draft was loaded from, or null for a row the operator
   *  added in this session and the table has never seen. */
  origin: Row | null;
};

/**
 * Editable drafts for a policy table that is saved as one upsert/delete pair.
 *
 * Each draft remembers the row it came from, which is the whole point: in
 * these tables the key is itself an editable field (`route_code`, and for
 * standards `metric_type`), so a rename is indistinguishable from any other
 * edit once the original value is gone. Deriving "which row was this?" from
 * the current draft instead silently loses the old key — the save upserts
 * the new one, names nothing to delete, and leaves the original row behind
 * still feeding the reports.
 */
export function useRowDrafts<Row, Draft>(rows: readonly Row[], toDraft: (row: Row) => Draft) {
  const [entries, setEntries] = useState<Entry<Row, Draft>[]>(() =>
    rows.map((row) => ({ draft: toDraft(row), origin: row })),
  );
  const [removed, setRemoved] = useState<Row[]>([]);

  function update<K extends keyof Draft>(index: number, key: K, value: Draft[K]) {
    setEntries((es) => es.map((e, i) => (i === index ? { ...e, draft: { ...e.draft, [key]: value } } : e)));
  }

  function add(draft: Draft) {
    setEntries((es) => [...es, { draft, origin: null }]);
  }

  function remove(index: number) {
    const origin = entries[index]?.origin;
    if (origin != null) setRemoved((rs) => [...rs, origin]);
    setEntries((es) => es.filter((_, i) => i !== index));
  }

  /** Every original row the save has to delete: those explicitly removed,
   *  plus those a rename would otherwise strand under their old key. */
  function deletions(renamed: (draft: Draft, origin: Row) => boolean): Row[] {
    const stranded = entries.flatMap((e) => (e.origin != null && renamed(e.draft, e.origin) ? [e.origin] : []));
    return [...removed, ...stranded];
  }

  return { entries, drafts: entries.map((e) => e.draft), update, add, remove, deletions };
}
