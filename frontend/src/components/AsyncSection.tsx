import type { ReactNode } from "react";
import { classifyError, isTransientErrorClass } from "../api/errorClass";
import { ErrorBanner } from "./ErrorBanner";
import { Skeleton } from "./Skeleton";
import { useAutoRetry } from "./useAutoRetry";

type Props<T> = {
  /** True while there is nothing to render yet. Which react-query flag feeds
   *  this is the caller's decision and it matters: `isPending` keeps whatever
   *  `keepPreviousData` held on screen across a refetch, while `isFetching`
   *  replaces it with the skeleton. Pass `isFetching` only where showing the
   *  previous response under new parameters would misread as the new one. */
  loading: boolean;
  error: unknown;
  onRetry?: () => void;
  data: T | undefined;
  /** Whether loaded data has anything worth rendering. Omit when any
   *  successful response is worth rendering. */
  hasContent?: (data: T) => boolean;
  /** Required, not optional: a section that silently renders nothing for an
   *  empty-but-successful response is the failure mode this component exists
   *  to make unrepresentable. */
  empty: ReactNode;
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
};

/**
 * The four branches every data-backed section needs — loading, failed, loaded
 * but empty, loaded with content — in one fixed order, so no two sections
 * disagree about which wins when more than one is live at once.
 *
 * Renders no element of its own: call sites drop it directly into grid and
 * flex containers where a wrapper would become a stray item.
 */
export function AsyncSection<T>({
  loading,
  error,
  onRetry,
  data,
  hasContent,
  empty,
  skeleton,
  children,
}: Props<T>) {
  const transient = error != null && isTransientErrorClass(classifyError(error));
  // A transient network/timeout blip gets two quiet retries behind the
  // section's normal loading chrome before it ever surfaces as an alarming
  // banner — see useAutoRetry. Every other error class (a standing
  // condition, or a real failure worth a manual decision) skips straight to
  // the banner, unchanged from before.
  const { retrying } = useAutoRetry(error != null, transient, onRetry);
  if (error != null) {
    if (retrying) return <>{skeleton ?? <Skeleton height={320} />}</>;
    return <ErrorBanner error={error} onRetry={onRetry} />;
  }
  if (loading) return <>{skeleton ?? <Skeleton height={320} />}</>;
  if (data === undefined) return null;
  if (hasContent && !hasContent(data)) return <>{empty}</>;
  return <>{children(data)}</>;
}
