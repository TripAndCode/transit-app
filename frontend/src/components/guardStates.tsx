import { ErrorBanner } from "./ErrorBanner";
import { Skeleton } from "./Skeleton";

/** What a route guard shows while its probe has produced no value: an error
 *  with a retry when the probe failed, a placeholder while it is in flight. */
export function ProbeError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div style={{ padding: 24 }}>
      <ErrorBanner error={error} onRetry={onRetry} />
    </div>
  );
}

export function GuardPlaceholder() {
  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 12, maxWidth: 720 }}>
      <Skeleton height={28} width="40%" />
      <Skeleton height={200} />
    </div>
  );
}
