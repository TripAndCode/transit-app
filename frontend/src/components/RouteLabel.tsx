import { useTranslation } from "react-i18next";
import { routeLabelParts, type RouteLabelParts } from "../api/routeDisplayName";
import { routeQualifierText, routeTerminalsText, routeWhereText, type RouteNames } from "../api/useRouteNames";
import { FILTER_SEPARATOR } from "../utils/format";
import "./RouteLabel.css";

/** A route's label: its short code as a plate, its line name beside it, and
 *  where it runs on a quiet second line. `compact` drops that second line, for
 *  pickers, table cells and other tight places; where it runs then moves into
 *  the tooltip. The feed's route_id means nothing to a reader, so it is kept
 *  in the tooltip and printed only as the last resort, when the route has
 *  neither a short nor a long name; the terminals tell apart the routes that
 *  share a short code.
 *
 *  `fallbackName` is a name the caller's own payload carries, read like a
 *  short name until the static routes load or when they lack the code; with
 *  neither, the label reads "Route <code>".
 *
 *  `plateColumn` is for a list that lines its routes' names up on one
 *  column whatever their plates (see RouteLabel.css); the list's grid sets
 *  the column. */
export function RouteLabel({
  code,
  names,
  fallbackName,
  compact = false,
  plateColumn = false,
}: {
  code: string;
  names: RouteNames;
  fallbackName?: string | null;
  compact?: boolean;
  plateColumn?: boolean;
}) {
  const parts =
    names.parts.get(code) ??
    (fallbackName
      ? routeLabelParts({ route_id: code, route_short_name: fallbackName, route_long_name: null }, names.agencyName)
      : null);
  if (parts == null) {
    // Bare text in the list's grid would land in the plate's track.
    return plateColumn ? (
      <span className="route-label route-label--full route-label--plate-column">
        <span className="route-label__line">{names.format(code)}</span>
      </span>
    ) : (
      <>{names.format(code)}</>
    );
  }
  return <RoutePartsLabel parts={parts} compact={compact} plateColumn={plateColumn} />;
}

/** `RouteLabel` for a caller that holds the label's parts rather than a code.
 *  A compact label still says where the route runs, inline and muted, when
 *  another route shares its plate and line name; `nameOnly` leaves that out
 *  for a caller that shows where the route runs beside the label itself. */
export function RoutePartsLabel({
  parts,
  compact = false,
  nameOnly = false,
  plateColumn = false,
}: {
  parts: RouteLabelParts;
  compact?: boolean;
  nameOnly?: boolean;
  plateColumn?: boolean;
}) {
  const { t } = useTranslation();
  const { plate, line, terminals, qualifier, ambiguous, id } = parts;
  const where = routeWhereText(parts, t);
  const twoLines = !compact && where != null;
  const aside = compact && ambiguous && !nameOnly ? where : null;
  const title = compact && where ? `${where}${FILTER_SEPARATOR}${id}` : id;
  const className = [
    "route-label",
    compact ? "route-label--compact" : "route-label--full",
    plate ? "route-label--plated" : "",
    twoLines ? "route-label--has-ends" : "",
    plateColumn ? "route-label--plate-column" : "",
  ].filter(Boolean).join(" ");
  const pieces = [
    terminals && <span key="ends">{routeTerminalsText(terminals, t)}</span>,
    terminals?.via && (
      <span key="via" className="route-label__via">
        {t("common.route_via", { via: terminals.via })}
      </span>
    ),
    qualifier && (
      <span key="qualifier" className={qualifier.via ? "route-label__via" : undefined}>
        {routeQualifierText(qualifier, t)}
      </span>
    ),
  ].filter(Boolean);
  return (
    <span className={className} title={title}>
      {plate && <span className="route-label__plate">{plate}</span>}
      {plate && line && " "}
      {/* Each line wraps once and clamps there: Japanese has no spaces, so a
          one-line ellipsis would cut a name mid-word. */}
      {line && <span className={compact ? "route-label__line" : "route-label__line clamp-2"}>{line}</span>}
      {aside && <span className="route-label__aside">{`${FILTER_SEPARATOR}${aside}`}</span>}
      {twoLines && (
        <>
          {" "}
          <span className="route-label__ends clamp-2">
            {pieces.flatMap((piece, i) => (i === 0 ? [piece] : [FILTER_SEPARATOR, piece]))}
          </span>
        </>
      )}
    </span>
  );
}
