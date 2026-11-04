"""The loaded timetable's content as analyze reads it: the columns its
timetable-reading builders join on, hashed per table without ordering. Shared by
analyze, whose fingerprints fold it in, and the timetable archive, which keys
each timetable it keeps by it."""

# Exactly the static-schedule columns read by the aggregates a run may scope
# or skip, directly or through an aggregate they read back. Too narrow silently
# keeps stale rows; too wide forces full rebuilds a reader would not have
# noticed, so the set tracks actual reads rather than whole tables:
#
#   static_stop_times  which physical stop a trip visit maps to
#     (agg_stop_daily, agg_route_stop_daily, agg_stop_routes), its scheduled
#     arrival/departure (agg_route_daily_dwell_run), and the departures
#     agg_route_headway's scheduled median is built from.
#   static_trips       the route_id bridge to that median, plus the service_id
#     it picks each route's dominant calendar by — a reimport that only
#     re-calendars existing trips moves the median without adding or removing
#     a single row.
#   static_routes      only route_id, which is where agg_route_headway's
#     route_code comes from; route_short_name has no reader here.
#
# agg_route_headway is skipped on a run with no changed date on the strength
# of these columns alone, and pipeline.static_headways computes each
# timetable's long-gap medians from the same inputs, keyed by the content these
# parts hash to, so the median's inputs have to be covered for both.
STATIC_DEPENDENCY_COLUMNS: dict[str, tuple[str, ...]] = {
    "static_stop_times": ("trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"),
    "static_trips": ("trip_id", "route_id", "service_id"),
    "static_routes": ("route_id",),
}


def static_parts(agency_id: int, conn, has_static: bool) -> list[str]:
    """The loaded timetable's content, one ``table:count:digest`` part per table.

    The date ledger sees rows arriving in ClickHouse and nothing else, but
    several aggregates also read the Postgres timetable. A load replaces those
    rows wholesale for the agency, so a new timetable changes what a past date
    of such a table should aggregate to while every per-date count stands
    still. These parts are agg_route_headway's fingerprint (it is built from
    the loaded timetable), the archive-joined tables' too for an agency whose
    ingest stamps no version (:func:`pipeline.analyze.table_fingerprints`), the
    key each archived timetable is kept under (pipeline.static_archive), and
    the inputs of its kept headway medians (pipeline.static_headways).

    An order-independent sum of per-row hashes plus a row count, not an ordered
    digest: the sum is one sequential scan of tables holding at most a few
    hundred thousand rows per agency, where an ordered digest would add a sort
    of all of them to every run.

    Computed here, on read, rather than once per import by whatever wrote the
    schedule. Caching it at write time would cost a fraction of this, but it
    would also make the signal only as good as every writer's memory to
    update it — and this value's whole job is to be trustworthy about rows
    this function did not watch arrive.

    `md5` rather than `hashtext`, which measures the same on this data: this
    value decides whether already-built aggregates are trusted, and
    `hashtext` is an internal function Postgres does not promise to keep
    stable across major versions — an upgrade would silently change every
    fingerprint. Sixteen hex digits of the digest is 64 bits per row against
    `hashtext`'s 32, and the count guards the one case a sum cannot see, rows
    appearing or disappearing in offsetting pairs.

    Columns are joined by the ASCII unit separator and NULL rendered as the
    record separator, neither of which a GTFS text field can carry: a plain
    join on a printable character would let a value containing it shift a
    field boundary, and rendering NULL as the empty string would make a NULL
    arrival time indistinguishable from an empty one, hiding a reload that
    only flipped between them. NUL itself is not available — Postgres `text`
    cannot hold it.

    Without a loaded schedule there are no parts: every aggregate that reads
    the static tables is inside a ``has_static`` branch of
    :func:`pipeline.analyze.analyze`, and querying those tables anyway would
    turn the missing-``static_*``-tables case analyze's ``_static_loaded``
    deliberately tolerates into a hard failure.
    """
    if not has_static:
        return []
    parts: list[str] = []
    with conn.cursor() as cur:
        for table, columns in STATIC_DEPENDENCY_COLUMNS.items():
            rendered = " || E'\\x1f' || ".join(f"coalesce({c}::text, E'\\x1e')" for c in columns)
            row_hash = f"('x' || substr(md5({rendered}), 1, 16))::bit(64)::bigint"
            cur.execute(
                f"SELECT count(*), coalesce(sum({row_hash}), 0) FROM {table} WHERE agency_id = %s",
                (agency_id,),
            )
            n, digest = cur.fetchone()
            parts.append(f"{table}:{n}:{digest}")
    return parts
