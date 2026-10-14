import type { Route } from "./types";

/**
 * Prefer route_short_name, then route_long_name, before falling back to the
 * raw route_id (which duplicates the parenthesised code inline and reads
 * worse than either GTFS name field). Mirrors the backend's own
 * COALESCE(NULLIF(short,''), NULLIF(long,''), code) order in
 * api/routers/reports.py's forecast_overview route-label query — empty
 * strings (not just null/undefined) count as "absent" on both sides.
 *
 * Single source of truth for this 3-way fallback: any caller should use
 * this instead of re-deriving the order, or the labels drift apart.
 */
export function routeDisplayName(route: Pick<Route, "route_short_name" | "route_long_name" | "route_id">): string {
  return route.route_short_name || route.route_long_name || route.route_id;
}

/** Where a route runs, from the feed's own terminals. A loop needs only its
 *  origin; `to` is null for one. */
export type RouteTerminals = { from: string; to: string | null; via: string | null; loop: boolean; bidirectional: boolean };

/** A route's one headsign, kept only to tell it apart from a route that
 *  would otherwise read the same. `via` marks a headsign that names a via
 *  point; its text is then the place alone. */
export type RouteQualifier = { text: string; via: boolean };

/** A route's label, in the pieces the UI draws: the short public code as a
 *  plate, the line name beside it, and where it runs beneath. */
export type RouteLabelParts = {
  plate: string | null;
  /** Null when it would only repeat the plate. */
  line: string | null;
  terminals: RouteTerminals | null;
  qualifier: RouteQualifier | null;
  /** Another route of the agency has the same plate and line name, so a
   *  one-line label has to say where this one runs to be told apart. */
  ambiguous: boolean;
  /** The feed's route_id: never shown in a list, kept for a tooltip. */
  id: string;
};

type RouteNameFields = Pick<Route, "route_id" | "route_short_name" | "route_long_name"> &
  Partial<Pick<Route, "origin_name" | "via_name" | "destination_name" | "is_loop" | "bidirectional">>;

/** A public route code ("70-3", "A1", "L21", "14H", "11-19") that opens a
 *  name field, followed by the rest of the name. */
const LEADING_CODE = /^([A-Za-z]?\d+[A-Za-z]?(?:-\d+)*[A-Za-z]?)\s+(.+)$/u;
/** A name field that is nothing but such a code. */
const WHOLE_CODE = /^[A-Za-z]?\d+[A-Za-z]?(?:-\d+)*[A-Za-z]?$/;
/** Longer codes ("904-14-01") are a feed's internal ids, not what is
 *  painted on the bus. */
const PLATE_MAX_LENGTH = 6;

function clean(value: string | null | undefined): string {
  return (value ?? "").trim();
}

/** Full-width Latin letters, digits and punctuation ("７０－３") and the
 *  ideographic space as their ASCII forms, for matching only: the label shows
 *  the name as the feed wrote it. Each character maps to exactly one, so an
 *  index into the result is an index into the original. */
function halfWidth(text: string): string {
  return text.replace(/[\uFF01-\uFF5E\u3000]/gu, (c) => (c === "\u3000" ? " " : String.fromCharCode(c.charCodeAt(0) - 0xfee0)));
}

function isPlate(code: string): boolean {
  return code.length <= PLATE_MAX_LENGTH && /\d/.test(halfWidth(code));
}

/** A name without the agency's own name in front of it. Only a whole leading
 *  word is the agency's: a brand that merely starts with the same text
 *  ("廿日市さくらバス" for an agency "廿日市") stays whole, and so does a name
 *  that is the agency name alone. */ // i18n-ignore: JSDoc example
function withoutAgencyName(name: string, agencyName: string | null | undefined): string {
  const agency = clean(agencyName);
  if (!agency || !name.startsWith(agency)) return name;
  const rest = name.slice(agency.length);
  if (!/^\s/u.test(rest)) return name;
  return rest.trim() || name;
}

/** Two names as one, without saying the same thing twice. */
function joinNames(a: string, b: string): string {
  if (!a || b.includes(a)) return b;
  if (!b || a.includes(b)) return a;
  return `${a} ${b}`;
}

/** Whether `name` holds `short`. A code counts only as a whole token, so
 *  "12号線" holds "12" while "Line 10" does not hold "1". */ // i18n-ignore: JSDoc example
function holdsShortName(name: string, short: string): boolean {
  const [text, code] = [halfWidth(name), halfWidth(short)];
  if (!WHOLE_CODE.test(code)) return text.includes(code);
  // `code` is letters, digits and hyphens only, so it is safe in a pattern.
  return new RegExp(`(?<![0-9A-Za-z-])${code}(?![0-9A-Za-z-])`).test(text);
}

/** The code that opens `name`, or that `name` is, as the feed wrote it. */
function splitCode(name: string): { code: string; rest: string } | null {
  const matchable = halfWidth(name);
  const lead = LEADING_CODE.exec(matchable);
  if (lead) return { code: name.slice(0, lead[1].length), rest: name.slice(matchable.length - lead[2].length) };
  return WHOLE_CODE.test(matchable) ? { code: name, rest: "" } : null;
}

function plateAndLine(route: RouteNameFields, agencyName: string | null | undefined): { plate: string | null; line: string | null } {
  const short = withoutAgencyName(clean(route.route_short_name), agencyName);
  const long = withoutAgencyName(clean(route.route_long_name), agencyName);
  const shortCode = splitCode(short);
  const longCode = splitCode(long);

  let plate: string | null = null;
  // The short name as it can be shown: an over-long code is an internal id
  // and says nothing, so it gives way to a long name when there is one.
  let shortName = short;
  if (shortCode && isPlate(shortCode.code)) {
    plate = shortCode.code;
    shortName = shortCode.rest;
  } else if (shortCode && !shortCode.rest && long) {
    shortName = "";
  }
  let longName = long === short ? "" : long;
  let codeFromLong = false;
  if (longCode && longCode.rest && isPlate(longCode.code) && (plate == null || halfWidth(plate) === halfWidth(longCode.code))) {
    plate = longCode.code;
    longName = longCode.rest;
    codeFromLong = true;
  }
  // A long name that already holds the short name says it all, unless the
  // plate came out of the long name itself.
  if (short && longName && holdsShortName(longName, short) && !codeFromLong) {
    return { plate: null, line: longName };
  }
  const line = joinNames(shortName, longName) || (plate ? "" : clean(route.route_id));
  return { plate, line: line || null };
}

function routeTerminals(route: RouteNameFields): RouteTerminals | null {
  const from = clean(route.origin_name);
  const to = clean(route.destination_name);
  const via = clean(route.via_name) || null;
  if (from && route.is_loop) return { from, to: null, via, loop: true, bidirectional: false };
  if (from && to) return { from, to, via, loop: false, bidirectional: !!route.bidirectional };
  return null;
}

/** One route's label, without a qualifier: that needs the agency's other
 *  routes (see `routeLabelPartsByCode`). */
export function routeLabelParts(route: RouteNameFields, agencyName?: string | null): RouteLabelParts {
  return { ...plateAndLine(route, agencyName), terminals: routeTerminals(route), qualifier: null, ambiguous: false, id: route.route_id };
}

const VIA_SUFFIX = "経由"; // i18n-ignore: a headsign suffix to read, not display text

/** A route's one headsign as a qualifier, or null when it has none or
 *  several. Agencies decorate headsigns with a route-code prefix glued to
 *  the place name and the line name in trailing parentheses; both are
 *  stripped, the prefix only when it runs straight into non-Latin text, so a
 *  Latin headsign is left alone. The rest stays as the feed wrote it: a
 *  headsign can name a via point, a destination, or both ends. */
function headsignQualifier(headsigns: string[]): RouteQualifier | null {
  const signs = [...new Set(headsigns.map((h) => h.trim()).filter(Boolean))];
  if (signs.length !== 1) return null;
  const text = signs[0]
    .replace(/[（(][^（()）]*[)）]\s*$/u, "")
    .replace(/^[A-Za-z]+\d*(?=\P{ASCII})/u, "")
    .trim();
  if (!text) return null;
  if (text.endsWith(VIA_SUFFIX) && text.length > VIA_SUFFIX.length) {
    return { text: text.slice(0, -VIA_SUFFIX.length).trim(), via: true };
  }
  return { text, via: false };
}

function qualifierKey(q: RouteQualifier | null): string {
  return q ? `${q.via ? "via:" : ""}${q.text}` : "";
}

/** route_code → label for an agency's routes. A route whose label matches
 *  another's takes its headsign as a qualifier, where that tells them apart;
 *  a route that already reads as its own shows no headsign. A route whose
 *  one-line name matches another's is marked `ambiguous`. */
export function routeLabelPartsByCode(routes: Route[], agencyName?: string | null): Map<string, RouteLabelParts> {
  const coded = routes.filter((r): r is Route & { route_code: string } => !!r.route_code);
  const labels = coded.map((r) => routeLabelParts(r, agencyName));
  const sameLabel = new Map<string, number[]>();
  labels.forEach((p, i) => {
    const key = JSON.stringify([p.plate, p.line, p.terminals]);
    sameLabel.set(key, [...(sameLabel.get(key) ?? []), i]);
  });
  for (const group of sameLabel.values()) {
    if (group.length < 2) continue;
    const qualifiers = group.map((i) => headsignQualifier(coded[i].trip_headsigns ?? []));
    if (new Set(qualifiers.map(qualifierKey)).size < 2) continue;
    group.forEach((i, j) => {
      labels[i] = { ...labels[i], qualifier: qualifiers[j] };
    });
  }
  const sameName = new Map<string, number>();
  for (const p of labels) sameName.set(routeNameText(p), (sameName.get(routeNameText(p)) ?? 0) + 1);
  return new Map(coded.map((r, i) => [r.route_code, { ...labels[i], ambiguous: (sameName.get(routeNameText(labels[i])) ?? 0) > 1 }]));
}

/** The label on one line, as plain text: "70-3 あさひが丘線". */ // i18n-ignore: JSDoc example
export function routeNameText(parts: Pick<RouteLabelParts, "plate" | "line" | "id">): string {
  return [parts.plate, parts.line].filter(Boolean).join(" ") || parts.id;
}

function shared(texts: string[]): Set<string> {
  const seen = new Map<string, number>();
  for (const text of texts) seen.set(text, (seen.get(text) ?? 0) + 1);
  return new Set([...seen].filter(([, n]) => n > 1).map(([text]) => text));
}

/** The keys of the routes whose full description another route also has, so
 *  that only their route_ids tell them apart. */
export function routesAlike(entries: { key: string; full: string }[]): Set<string> {
  const alike = shared(entries.map((e) => e.full));
  return new Set(entries.filter((e) => alike.has(e.full)).map((e) => e.key));
}

/** Plain-text labels for a list of routes, as an `<option>` has to hold
 *  them: each route's name; where it runs, only where two names match; and
 *  its route_id, only where even that leaves two alike. Several routes can
 *  share a short code, so a name alone does not always tell them apart. */
export function distinctRouteTexts(entries: { key: string; name: string; full: string; id: string }[]): Map<string, string> {
  const sharedNames = shared(entries.map((e) => e.name));
  const first = entries.map((e) => (sharedNames.has(e.name) ? e.full : e.name));
  const sharedFirst = shared(first);
  return new Map(entries.map((e, i) => [e.key, sharedFirst.has(first[i]) ? `${first[i]} (${e.id})` : first[i]]));
}
