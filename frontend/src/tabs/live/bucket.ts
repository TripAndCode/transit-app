/**
 * Pure grouping helper for the 最新観測 triage list: partitions routes into the
 * fixed severity-bucket order and orders rows within each bucket.
 */
import type { RouteSummary, RouteBucket } from "../../api/types";

export const BUCKET_ORDER: RouteBucket[] = ["anomaly", "watch", "normal", "no_baseline"];

export type BucketGroup = { bucket: RouteBucket; routes: RouteSummary[] };

/** Group routes into the fixed bucket order. Baseline buckets sort by
 *  deviation desc; `no_baseline` (no deviation) sorts by raw worst delay desc.
 *  Always returns all four buckets (possibly empty) so counts render uniformly. */
export function groupByBucket(routes: RouteSummary[]): BucketGroup[] {
  return BUCKET_ORDER.map((bucket) => {
    const inBucket = routes.filter((r) => r.bucket === bucket);
    inBucket.sort((a, b) =>
      bucket === "no_baseline"
        ? b.worst_delay_sec - a.worst_delay_sec
        : (b.deviation_sec ?? 0) - (a.deviation_sec ?? 0),
    );
    return { bucket, routes: inBucket };
  });
}
