import type { useRouteNames } from "../api/useRouteNames";
import "./RouteLabel.css";

/** A route's label in a list of routes. Variants of one line can share a
 *  label, so the GTFS code follows it as quiet secondary text.
 *
 *  `fallbackName` is a name the caller's own payload carries, shown until the
 *  static routes load or when they lack the code; with neither, the code
 *  reads as "Route <code>" and gets nothing more. */ // i18n-ignore: JSDoc example
export function RouteLabel({
  code,
  names,
  fallbackName,
}: {
  code: string;
  names: ReturnType<typeof useRouteNames>;
  fallbackName?: string | null;
}) {
  const label = names.data.get(code) ?? (fallbackName || null);
  if (label == null) return <>{names.format(code)}</>;
  // A route with no name is labelled by its route_id, which embeds the code.
  if (label.includes(`(${code})`)) return <>{label}</>;
  return (
    <>
      {label} <span className="route-label__code">{code}</span>
    </>
  );
}
