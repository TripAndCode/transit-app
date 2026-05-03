/**
 * Format an ISO timestamp as a calm Japanese relative-time string.
 * Returns "—" for invalid or future-dated input (clock skew tolerant).
 */
export function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(diff) || diff < 0) return "—";
  if (diff < 60) return `${Math.floor(diff)}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}時間前`;
  return `${Math.floor(diff / 86400)}日前`;
}
