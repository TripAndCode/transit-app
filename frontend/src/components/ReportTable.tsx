import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import type { TFunction } from "i18next";
import { DELAY_THRESHOLDS, delayColor } from "../styles/tokens";
import { useRouteNames, type RouteNames } from "../api/useRouteNames";
import { RouteLabel } from "./RouteLabel";
import { PendingNavLink } from "./navPending";
import { usePendingNavTarget } from "./navPendingContext";
import { RouteTitleTransition } from "./RouteTitleTransition";
import { isPlainLeftClick } from "../utils/clicks";
import { prefersReducedMotion } from "../utils/motion";
import { ServiceName } from "./ServiceName";
import { serviceValueLabel } from "../utils/filterValueLabels";
import { routeHref, type RouteTableSort } from "../routes/destinations";
import { useAgencyId } from "../api/useAgencyId";
import { SHARED_TABLE, th, td } from "./tableStyles";
import { useCappedList } from "../hooks/useCappedList";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { Z_INDEX } from "../styles/zIndex";
import { formatDuration, roundTenth } from "../utils/format";
import "./ReportTable.css";
import { ServiceNote } from "./ServiceNote";
import type { RowMark } from "./snapshotCompare";
import { Tooltip } from "./Tooltip";
import { isFewData } from "./fewData";
import { SCHEMAS, type Schema } from "./reportTableSchemas";

const ROWS_CAP = 200;
// A phone list item is several times a table row's height, so the first
// screenful is shorter.
const PHONE_ROWS_CAP = 25;

// Module-scope pure function rather than an in-render IIFE — see
// eslint.config.js's manual-memoization ban comment for why this shape is
// preferred over an inline immediately-invoked function expression.
function computeColumnMaxes(schema: Schema[] | undefined, rows: unknown[][]): Map<number, number> {
  if (!schema) return new Map<number, number>();
  const m = new Map<number, number>();
  for (const col of schema) {
    if (!col.bar) continue;
    // A percentage has a fixed whole; scaled to the column's best instead, a
    // list of low rates would draw its top row as a full bar.
    if (col.bar === "pct") {
      m.set(col.index, 100);
      continue;
    }
    let mx = 0;
    for (const row of rows) {
      const v = Number(row[col.index]);
      if (isFinite(v) && Math.abs(v) > mx) mx = Math.abs(v);
    }
    m.set(col.index, mx || 1);
  }
  return m;
}

// The share of the table a compact route column takes: the room where a
// route runs has before its ellipsis, beside figures that never wrap.
const COMPACT_ROUTE_SHARE = "45%";

// The route column stays in view while a table wider than its panel
// scrolls sideways. It needs an opaque background for rows to pass under:
// the page's, since the table sits directly on it.
const STICKY_CELL = { position: "sticky", left: 0, zIndex: Z_INDEX.raised, background: "var(--bg-page)" } as const;
const STICKY_HEAD = { position: "sticky", left: 0, zIndex: Z_INDEX.raised, background: "var(--bg-soft)" } as const;

type Props = {
  reportType: string;
  rows: unknown[][];
  /** The observation count below which a row is badged as thinly observed. */
  minSamples?: number | null;
  /** The Routes table's sort: its column carries the bar and the weight, and
   *  heads each card on a phone. */
  sortBy?: RouteTableSort;
  /** Which way the sorted column runs, for assistive technology. */
  sortDirection?: "ascending" | "descending";
  /** The threshold an early-departure column counts past, from the
   *  response's definition; without it that column is left out. */
  earlyDepartureSec?: number | null;
  /** Route labels to draw the rows with, in place of the agency's current
   *  ones: a frozen snapshot keeps the catalogue it was taken with. */
  names?: RouteNames;
  /** A snapshot's rows marked against today's figures, one mark per row
   *  (snapshotCompare.ts): a figure that moved prints today's beside it, and
   *  a row only one side lists says which. A row only today lists has no
   *  rank in the snapshot's order. */
  marks?: readonly RowMark[];
  /** A caller's own mark after a column's figure, by the column's row index:
   *  the Routes table's comparison of a route's on-time rate with the
   *  route's own reference. */
  cellMarks?: Readonly<Record<number, (row: unknown[]) => Caveat | undefined>>;
  /** Whether the statistics-only columns show: the reader's setting on a
   *  screen that offers the toggle. */
  showStatistics: boolean;
};

/** A mark after a figure: a muted caveat, or with `tone: "reference"` a
 *  neutral chip comparing the figure with a reference. */
type Caveat = { mark: string; tip?: string; tone?: "reference" };

export function ReportTable(props: Props) {
  return props.names ? <Table {...props} names={props.names} /> : <TableWithAgencyNames {...props} />;
}

/** The open agency's current route labels, read only when no others are
 *  given, so a snapshot's page asks the API for nothing beyond itself. */
function TableWithAgencyNames(props: Props) {
  const names = useRouteNames(useAgencyId());
  return <Table {...props} names={names} />;
}

function Table({
  reportType,
  rows,
  minSamples,
  sortBy,
  sortDirection,
  earlyDepartureSec,
  names,
  marks,
  cellMarks,
  showStatistics,
}: Props & { names: RouteNames }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const { search } = useLocation();
  const fullSchema = SCHEMAS[reportType]?.filter((c) => !c.early || earlyDepartureSec != null);
  const schema = fullSchema?.filter((c) => showStatistics || !c.stat);
  // The visible row last clicked to travel; by row, not route, since a route
  // can fill several rows (a delay certificate lists each departure).
  const [travelling, setTravelling] = useState<number | null>(null);

  const compact = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  const cappedRows = useCappedList(
    rows,
    compact ? PHONE_ROWS_CAP : ROWS_CAP,
    `${reportType}:${compact ? "phone" : "wide"}`,
  );

  if (!schema) {
    // Unknown type: render nothing.
    return null;
  }

  const label = (c: Schema) => (c.early ? t(c.labelKey, { early: formatDuration(earlyDepartureSec) }) : t(c.labelKey));
  const basis = sortBy == null ? undefined : schema.find((c) => c.sort === sortBy);
  const barred = (c: Schema) => c.bar != null && (basis == null || c === basis);
  const serviceNote = schema.some((c) => c.service) && <ServiceNote />;
  // The few-data badge warns against trusting a row's figures, so it stays
  // in view with the observation count hidden: on the sorted figure, else
  // the first figure.
  const samplesCol = fullSchema?.find((c) => c.samples);
  const thinHost = schema.find((c) => c.samples) ?? basis ?? schema.find((c) => !c.route);
  const caveatsOf = (c: Schema, row: unknown[]): Caveat[] => {
    const own = c.caveat?.(row, t);
    const thin = c === thinHost && samplesCol != null && isFewData(row[samplesCol.index], minSamples);
    const caller = cellMarks?.[c.index]?.(row);
    return [...(own ? [own] : []), ...(thin ? [{ mark: t("reports.confidence_low_mark") }] : []), ...(caller ? [caller] : [])];
  };
  const sideOf = (i: number) => {
    const side = marks?.[i]?.side;
    return side === "snapshot" || side === "today" ? side : undefined;
  };
  // Today's figure for row i's column c, worded for the cell, where it
  // prints differently from the row's own.
  const todayOf = (i: number, c: Schema, format: (c: Schema, raw: unknown, t: TFunction) => string) => {
    const mark = marks?.[i];
    if (mark?.side !== "both" || !mark.today.has(c.index)) return undefined;
    return t("snapshot.compare.today_value", { value: format(c, mark.today.get(c.index), t) });
  };
  const showMore = cappedRows.remaining > 0 && (
    <button type="button" className="btn btn--secondary btn--sm" onClick={cappedRows.showMore}>
      {t("common.show_more", { count: cappedRows.remaining })}
    </button>
  );

  if (compact) {
    // A wide table's columns can't share a phone's width without characters stacking,
    // so each row becomes one item: rank, route and the sorted (else the
    // barred) figure on top, every other column on a wrapping line beneath.
    // A report without a route column has no title.
    const titleCol = schema.find((c) => c.route);
    const headline = basis ?? schema.find((c) => c.bar);
    return (
      <div>
        <ol className="report-cards">
          {cappedRows.visible.map((row, i) => (
            <li key={i} className="report-cards__item">
              <div className="report-cards__head">
                <span className="report-cards__rank">{sideOf(i) === "today" ? "" : i + 1}</span>
                {titleCol && (
                  <span className="report-cards__route">
                    <RouteCell agencyId={id} code={String(row[titleCol.index] ?? "")} names={names} compact={titleCol.route === "compact"} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                    <RowSide side={sideOf(i)} />
                  </span>
                )}
                {headline && (
                  <span className="report-cards__headline">
                    <span className="report-cards__label">{label(headline)}</span>{" "}
                    {headline.severeMarker && isSevere(row[headline.index]) && <DelayMarker />}
                    <CellValue column={headline} raw={row[headline.index]} text={cardValue(headline, row[headline.index], t)} caveats={caveatsOf(headline, row)} today={todayOf(i, headline, cardValue)} />
                  </span>
                )}
              </div>
              <p className="report-cards__meta">
                {schema
                  .filter((c) => c !== titleCol && c !== headline)
                  .map((c) => {
                    // A blank table cell reads as nothing under its header;
                    // on a card the label would stand alone.
                    const text = cardValue(c, row[c.index], t);
                    if (text === "") return null;
                    return (
                      <span key={c.labelKey} className="report-cards__field">
                        {!c.service && (
                          <>
                            <span className="report-cards__label">{label(c)}</span>{" "}
                          </>
                        )}
                        <CellValue column={c} raw={row[c.index]} text={text} caveats={caveatsOf(c, row)} today={todayOf(i, c, cardValue)} />
                      </span>
                    );
                  })}
              </p>
            </li>
          ))}
        </ol>
        {showMore}
        {serviceNote}
      </div>
    );
  }

  // Scaled to the snapshot's own rows, so a route only today lists leaves
  // the stored rows' bars as they were.
  const maxes = computeColumnMaxes(schema, marks ? rows.filter((_, i) => marks[i]?.side !== "today") : rows);
  return (
    <div className="table-scroll" style={{ width: "100%", overflowX: "auto" }}>
      <table style={SHARED_TABLE}>
        <thead>
          <tr style={{ background: "var(--bg-soft)" }}>
            <th style={{ ...th({ width: 40 }), whiteSpace: "nowrap" }}>#</th>
            {schema.map((c) => (
              <th
                key={c.labelKey}
                className={c === basis ? "report-col--basis" : undefined}
                aria-sort={c === basis ? sortDirection : undefined}
                // A header names its column on one line; a wrapped one
                // stands taller than every row under it.
                style={{
                  ...th(),
                  textAlign: c.align ?? "left",
                  whiteSpace: "nowrap",
                  ...(c === basis ? { color: "var(--text-primary)" } : null),
                  ...(c.route === "compact" ? { width: COMPACT_ROUTE_SHARE } : null),
                  ...(c === schema[0] && c.route ? STICKY_HEAD : null),
                }}
              >
                {c.unit ? t("reports.col.with_unit", { label: label(c), unit: t("common.unit_min") }) : label(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cappedRows.visible.map((row, i) => (
            <tr
              key={i}
              className={schema.some((c) => c.route) ? "report-row--link" : undefined}
              style={{ borderTop: "1px solid var(--border-soft)" }}
            >
              <td style={{ ...td({ align: "right" }), color: "var(--text-tertiary)" }}>{sideOf(i) === "today" ? "" : i + 1}</td>
              {schema.map((c) => {
                if (c.route) {
                  return (
                    <td
                      key={c.labelKey}
                      className={c.route === "compact" ? "report-route-cell--compact" : undefined}
                      // A full label names the line and where it goes; below
                      // this width it breaks onto a third line. A compact
                      // label holds one line.
                      style={{
                        ...td(),
                        ...(c === schema[0] ? STICKY_CELL : null),
                        ...(c.route === "compact" ? { minWidth: "15em", width: COMPACT_ROUTE_SHARE } : { minWidth: "16em" }),
                        fontWeight: 500,
                        wordBreak: "keep-all",
                      }}
                    >
                      <RouteCell agencyId={id} code={String(row[c.index] ?? "")} names={names} compact={c.route === "compact"} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                      <RowSide side={sideOf(i)} />
                    </td>
                  );
                }
                const raw = row[c.index];
                const value = <CellValue column={c} raw={raw} text={cellText(c, raw, t)} caveats={caveatsOf(c, row)} today={todayOf(i, c, cellText)} />;
                const className = c === basis ? "report-cell--basis" : undefined;
                if (barred(c)) {
                  // A delay bar is drawn from the minutes the cell prints, so
                  // two rows printing the same figure share a colour.
                  const v = c.bar === "delay" ? shownMinutes(raw) : Number(raw);
                  const max = maxes.get(c.index) ?? 1;
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  return (
                    <td key={c.labelKey} className={className} style={td({ align: c.align ?? "right" })}>
                      <BarCell
                        ratio={ratio}
                        color={c.bar === "delay" ? delayColor(v) : "var(--accent)"}
                        marked={!!c.severeMarker && isSevere(raw)}
                      >
                        {value}
                      </BarCell>
                    </td>
                  );
                }
                return (
                  <td key={c.labelKey} className={className} style={td({ align: c.align ?? "left" })}>
                    {value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {showMore}
      {serviceNote}
    </div>
  );
}

/** A row's route, as a link to its page: rows are where a route is found,
 *  so they are how it is opened. The scope carries over; routeHref drops what
 *  only chose this screen's report.
 *
 *  It opens as a screen navigation, and a plain click also lends the row's
 *  label the dossier title's transition name so the label travels into the
 *  title. The name lasts only while that navigation is pending, so one that
 *  is superseded leaves no stale name for a later transition to pair with.
 *  Under reduced motion nothing travels; a modified click stays the
 *  browser's. */
function RouteCell({
  agencyId,
  code,
  names,
  compact,
  search,
  travels,
  onTravel,
}: {
  agencyId: number | null;
  code: string;
  names: ReturnType<typeof useRouteNames>;
  compact: boolean;
  search: string;
  travels: boolean;
  onTravel: () => void;
}) {
  const pendingTo = usePendingNavTarget();
  if (agencyId == null || !code) return <RouteLabel code={code} names={names} compact={compact} />;
  const href = routeHref(agencyId, code, search);
  const travelling = travels && pendingTo === href;
  const link = (
    <PendingNavLink
      className={travelling ? "report-route-link report-route-link--travelling" : "report-route-link"}
      to={href}
      spinner={false}
      onClick={(e) => {
        if (isPlainLeftClick(e) && !prefersReducedMotion()) onTravel();
      }}
    >
      <RouteLabel code={code} names={names} compact={compact} />
      <span className="report-route-link__chevron" aria-hidden="true">
        {" ›"}
      </span>
    </PendingNavLink>
  );
  return travelling ? <RouteTitleTransition>{link}</RouteTitleTransition> : link;
}

function cellText(c: Schema, raw: unknown, t: TFunction): string {
  if (c.format) return c.format(raw, t);
  if (c.service && raw != null) return serviceValueLabel(String(raw), t);
  return String(raw ?? "—");
}

/** A card has no column header to carry the unit, so the value does. */
function cardValue(c: Schema, raw: unknown, t: TFunction): string {
  const text = cellText(c, raw, t);
  return c.unit && raw != null ? t("reports.card.value_with_unit", { value: text, unit: t("common.unit_min") }) : text;
}

/** A plain cell: a service in the UI's language where it has copy, anything
 *  else as its formatted text, followed by the row's caveats on it as muted
 *  badges, then, on a snapshot compared with today, today's figure where it
 *  moved. A badge with a tip takes keyboard focus, so the tip is reachable
 *  without a pointer. */
function CellValue({
  column,
  raw,
  text,
  caveats,
  today,
}: {
  column: Schema;
  raw: unknown;
  text: string;
  caveats: Caveat[];
  today?: string;
}) {
  if (column.service && raw != null) return <ServiceName value={String(raw)} />;
  return (
    <>
      {text}
      {caveats.map(({ mark, tip, tone }) => (
        <span key={mark}>
          {" "}
          {tip ? (
            <Tooltip label={tip}>
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- plain badge, not a control; keyboard-focusable only so what it stands for is reachable */}
              <span className={tone === "reference" ? "reference-chip" : "caveat-badge"} tabIndex={0}>
                {mark}
              </span>
            </Tooltip>
          ) : (
            <span className={tone === "reference" ? "reference-chip" : "caveat-badge"}>{mark}</span>
          )}
        </span>
      ))}
      {today != null && (
        <>
          {" "}
          <span className="figure-today">{today}</span>
        </>
      )}
    </>
  );
}

/** Which side alone lists a row, on a snapshot compared with today. */
function RowSide({ side }: { side: "snapshot" | "today" | undefined }) {
  const { t } = useTranslation();
  if (side == null) return null;
  return (
    <>
      {" "}
      <span className="caveat-badge">{t(side === "snapshot" ? "snapshot.compare.only_snapshot" : "snapshot.compare.only_today")}</span>
    </>
  );
}

/** The minutes a delay cell prints: its colour and bar are drawn from the
 *  same rounded figure as its text, never from the unrounded one. */
function shownMinutes(raw: unknown): number {
  return roundTenth(Number(raw));
}

/** At or past the colour ramp's severe threshold, as printed. */
function isSevere(raw: unknown): boolean {
  const v = shownMinutes(raw);
  return Number.isFinite(v) && v >= DELAY_THRESHOLDS.severe;
}

function DelayMarker() {
  return <span data-testid="delay-marker" className="delay-marker" aria-hidden="true" />;
}

function BarCell({
  ratio,
  color,
  marked,
  children,
}: {
  ratio: number;
  color: string;
  marked: boolean;
  children: ReactNode;
}) {
  return (
    // The bar has its own track beside the figure: drawn under the text, it
    // read as a strike-through.
    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
      <div
        data-testid="bar-track"
        aria-hidden="true"
        style={{ width: 40, height: 6, flex: "none", borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}
      >
        <div style={{ width: `${ratio * 100}%`, height: "100%", borderRadius: 3, background: color, opacity: 0.6 }} />
      </div>
      {marked && <DelayMarker />}
      <span>{children}</span>
    </div>
  );
}
