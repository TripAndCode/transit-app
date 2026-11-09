import { createSnapshot, fetchSnapshot, type SnapshotSpec } from "../../api/snapshots";
import type { ReportResponse } from "../../api/types";
import type { Locale } from "../../i18n";

/** How long a print waits for its snapshot before it goes ahead without one.
 *  Taking a snapshot computes the report once per language, so it can take a
 *  while on a scope the aggregates cannot serve; the print the reader asked
 *  for is held no longer than this. */
export const PRINT_SNAPSHOT_WAIT_MS = 20_000;

/** What a print's snapshot came to: stored with exactly the figures on the
 *  page (its page and method then go on paper), stored with figures other
 *  than the page's (the aggregates moved after the page loaded), or not
 *  stored and confirmed at all. */
export type PrintSnapshot =
  | { kind: "stored"; path: string; methodVersion: string }
  | { kind: "moved" }
  | { kind: "failed" };

/** A value with every object's keys in one order: the stored payload comes
 *  back through jsonb, which keeps its own key order. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical((value as Record<string, unknown>)[key])]),
    );
  }
  return value;
}

/** Whether two responses of a report endpoint say the same thing: everything
 *  but the moment each was served. */
export function sameFigures(a: ReportResponse, b: ReportResponse): boolean {
  const said = ({ rendered_at: _servedAt, ...rest }: ReportResponse) => JSON.stringify(canonical(rest));
  return said(a) === said(b);
}

/** Takes the snapshot an admin's print carries: stores `spec`, reads the
 *  snapshot back, and checks its response in the page's language is the
 *  response on the page, `shown`. The server recomputes the figures for the
 *  query, so a page loaded before a rebuild would otherwise carry an id for
 *  figures it does not show. Never throws, and settles within
 *  PRINT_SNAPSHOT_WAIT_MS. */
export async function snapshotForPrint(spec: SnapshotSpec, shown: ReportResponse, locale: Locale): Promise<PrintSnapshot> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PRINT_SNAPSHOT_WAIT_MS);
  try {
    const created = await createSnapshot(spec, { signal: controller.signal });
    const stored = (await fetchSnapshot(created.snapshot_id, { signal: controller.signal })).payloads.report?.[locale];
    if (stored == null) return { kind: "failed" };
    if (!sameFigures(stored, shown)) return { kind: "moved" };
    return { kind: "stored", path: created.path, methodVersion: created.method_version };
  } catch {
    return { kind: "failed" };
  } finally {
    clearTimeout(timer);
  }
}
