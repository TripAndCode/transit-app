"""Legacy SQL executors used by the v1 /api/{id}/query endpoint and the
v2 LLM tool surface (pipeline/query/tools.py). Each `_exec_*` returns a
list of tuples shaped to match the formatters in pipeline/query/formatter.py.

This module shares its dedup SQL with pipeline/db.py — see
build_dedup_inner_sql.
"""

from __future__ import annotations

import logging
import re

from pipeline.db import _DOW_JP_TO_ISO, build_dedup_inner_sql

_log = logging.getLogger(__name__)

_DEDUP_INNER = build_dedup_inner_sql(placeholder="$1")


async def _agg_loaded(conn, agency_id: int) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM agg_route_stats WHERE agency_id=$1 LIMIT 1", agency_id)
    return row is not None


async def _static_loaded(conn, agency_id: int) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM static_stops WHERE agency_id=$1 LIMIT 1", agency_id)
    return row is not None


async def _route_codes_from_name(route_name: str, conn, agency_id: int) -> list[str]:
    if not route_name:
        return []
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT u.route_code FROM updates u "
            "JOIN static_routes sr ON sr.route_id LIKE '%(' || u.route_code || ')' "
            "WHERE u.agency_id=$1 AND sr.agency_id=$1 "
            "AND (sr.route_short_name=$2 OR sr.route_short_name LIKE $3 OR sr.route_id LIKE $3) "
            "ORDER BY u.route_code",
            agency_id,
            route_name,
            f"%{route_name}%",
        )
        codes = [r["route_code"] for r in rows]
    except Exception as exc:
        _log.warning("_route_codes_from_name DB error for %r: %s", route_name, exc)
        codes = []
    if not codes:
        codes = re.findall(r"\((\d+)\)", route_name)
    return sorted(set(str(c) for c in codes))


async def _route_codes_from_intent(intent: dict, conn, agency_id: int) -> list[str]:
    route = intent.get("route")
    if route:
        return [str(route)]
    route_name = intent.get("route_name")
    if route_name:
        return await _route_codes_from_name(str(route_name), conn, agency_id)
    return []


def _order_keyword(sort_order: str, default: str = "DESC") -> str:
    if sort_order == "asc":
        return "ASC"
    if sort_order == "desc":
        return "DESC"
    return default


def _route_in_clause(route_codes: list, n: int) -> tuple[str, list, int]:
    """Return (sql_fragment, values, next_n). Params numbered $n, $n+1, ..."""
    phs = ", ".join(f"${n + i}" for i in range(len(route_codes)))
    return f"route_code IN ({phs})", list(route_codes), n + len(route_codes)


# Time-band ranges as (start_inclusive, end_exclusive) text pairs; night
# and rush use four bounds to express the wrap-midnight / two-window shape.
# Values are bound as TEXT and cast server-side — see _time_band_clause for
# why the bind type matters.
_TIME_BAND_RANGES: dict[str, list[str]] = {
    "morning": ["05:00", "10:00"],
    "day": ["10:00", "16:00"],
    "evening": ["16:00", "20:00"],
    "night": ["20:00", "05:00"],
    "rush": ["07:00", "10:00", "17:00", "20:00"],
}


def _time_band_clause(time_band: str | None, n: int, col: str = "scheduled_time") -> tuple[str, list, int]:
    """Return ``(sql_fragment, values, next_n)`` for a named time-band filter.

    Each bind is cast as ``(${n}::text)::time`` so asyncpg sends the
    Python ``str`` over the wire as TEXT (avoiding asyncpg's prepared-
    statement type inference, which would otherwise try to encode the
    string as ``datetime.time`` and fail). The server then parses the
    text into TIME for the comparison against ``col``, which is itself
    cast to TIME to be correct both before and after migration 0011.
    """
    if not time_band or time_band not in _TIME_BAND_RANGES:
        return "", [], n
    vals = _TIME_BAND_RANGES[time_band]
    if time_band == "night":
        # Wraps midnight: hour >= 20:00 OR hour < 05:00.
        return (
            f"({col}::time >= (${n}::text)::time OR {col}::time < (${n + 1}::text)::time)",
            vals,
            n + 2,
        )
    if time_band == "rush":
        return (
            f"(({col}::time >= (${n}::text)::time AND {col}::time < (${n + 1}::text)::time)"
            f" OR ({col}::time >= (${n + 2}::text)::time AND {col}::time < (${n + 3}::text)::time))",
            vals,
            n + 4,
        )
    return (
        f"{col}::time >= (${n}::text)::time AND {col}::time < (${n + 1}::text)::time",
        vals,
        n + 2,
    )


def _dow_group_values(dow_group: str | None) -> list[int]:
    """ISODOW ints (Mon=1..Sun=7) for weekday / weekend rollups."""
    if dow_group == "weekend":
        return [6, 7]  # Sat, Sun
    if dow_group == "weekday":
        return [1, 2, 3, 4, 5]  # Mon-Fri
    return []


# ---------------------------------------------------------------------------
# _exec_ranking
# ---------------------------------------------------------------------------


async def _exec_ranking(intent: dict, conn, agency_id: int) -> list:
    service = intent.get("service")
    limit = intent.get("limit", 15)
    order = _order_keyword(intent.get("sort_order"), default="DESC")

    if await _agg_loaded(conn, agency_id):
        if service:
            rows = await conn.fetch(
                "SELECT route_code, service_type, avg_min, p50_min, p90_min, samples "
                f"FROM agg_route_stats WHERE agency_id=$1 AND service_type=$2 "
                f"ORDER BY avg_min {order} LIMIT $3",
                agency_id,
                service,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT route_code, '全日' AS service_type, "
                "SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
                "MAX(CASE WHEN service_type = '平日'  THEN avg_min END) AS heijitsu_avg, "
                "MAX(CASE WHEN service_type = '土日祝' THEN avg_min END) AS kyujitsu_avg, "
                "SUM(samples) AS samples "
                f"FROM agg_route_stats WHERE agency_id=$1 "
                f"GROUP BY route_code ORDER BY SUM(avg_min * samples) / NULLIF(SUM(samples), 0) {order} LIMIT $2",
                agency_id,
                limit,
            )
        return [tuple(r) for r in rows]

    # raw fallback — _DEDUP_INNER uses $1=agency_id, so outer params start at $2
    if service:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}),\n"
            "ranked AS (\n"
            "    SELECT *, PERCENT_RANK() OVER (PARTITION BY route_code, service_type ORDER BY dep_delay) AS pct\n"
            "    FROM deduped\n"
            ")\n"
            "SELECT route_code, service_type,\n"
            "    ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
            "    ROUND(MIN(CASE WHEN pct>=0.5 THEN dep_delay END)/60.0::numeric, 2) AS p50_min,\n"
            "    ROUND(MIN(CASE WHEN pct>=0.9 THEN dep_delay END)/60.0::numeric, 2) AS p90_min,\n"
            "    COUNT(*) AS samples\n"
            "FROM ranked WHERE service_type=$2\n"
            "GROUP BY route_code, service_type HAVING COUNT(*) > 20\n"
            f"ORDER BY avg_min {order} LIMIT $3",
            agency_id,
            service,
            limit,
        )
    else:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}),\n"
            "per_svc AS (\n"
            "    SELECT route_code, service_type,\n"
            "        ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
            "        COUNT(*) AS samples\n"
            "    FROM deduped GROUP BY route_code, service_type HAVING COUNT(*) > 20\n"
            ")\n"
            "SELECT route_code, '全日' AS service_type,\n"
            "    SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min,\n"
            "    MAX(CASE WHEN service_type = '平日'  THEN avg_min END) AS heijitsu_avg,\n"
            "    MAX(CASE WHEN service_type = '土日祝' THEN avg_min END) AS kyujitsu_avg,\n"
            "    SUM(samples) AS samples\n"
            "FROM per_svc\n"
            f"GROUP BY route_code ORDER BY SUM(avg_min * samples) / NULLIF(SUM(samples), 0) {order} LIMIT $2",
            agency_id,
            limit,
        )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_by_hour
# ---------------------------------------------------------------------------


async def _exec_by_hour(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []

    service = intent.get("service")
    time_band = intent.get("time_band")
    order = _order_keyword(intent.get("sort_order"), default="DESC")

    route_cond, route_vals, n = _route_in_clause(route_codes, 2)  # $2...$N

    conds = ["agency_id=$1", route_cond]
    params: list = [agency_id] + route_vals

    if service:
        conds.append(f"service_type=${n}")
        params.append(service)
        n += 1

    tb_cond, tb_vals, n = _time_band_clause(time_band, n)
    if tb_cond:
        conds.append(tb_cond)
        params.extend(tb_vals)

    where_sql = " AND ".join(conds)

    if await _agg_loaded(conn, agency_id):
        rows = await conn.fetch(
            "SELECT route_code, service_type, scheduled_time, avg_min, p50_min, p90_min, samples "
            f"FROM agg_route_hour WHERE {where_sql} ORDER BY avg_min {order}",
            *params,
        )
        return [tuple(r) for r in rows]

    # raw fallback: rebuild outer WHERE without agency_id (already in CTE)
    outer_conds = [route_cond]
    outer_params_vals = route_vals[:]
    outer_n = 2 + len(route_vals)

    if service:
        outer_conds.append(f"service_type=${outer_n}")
        outer_params_vals.append(service)
        outer_n += 1
    if tb_cond:
        # rebuild time_band clause with correct n
        tb_cond2, tb_vals2, _ = _time_band_clause(time_band, outer_n)
        outer_conds.append(tb_cond2)
        outer_params_vals.extend(tb_vals2)

    outer_where = " AND ".join(outer_conds)
    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}),\n"
        "ranked AS (\n"
        "    SELECT *, PERCENT_RANK() OVER (PARTITION BY route_code, service_type, scheduled_time ORDER BY dep_delay) AS pct\n"  # noqa: E501
        "    FROM deduped\n"
        ")\n"
        "SELECT route_code, service_type, scheduled_time,\n"
        "    ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "    ROUND(MIN(CASE WHEN pct>=0.5 THEN dep_delay END)/60.0::numeric, 2) AS p50_min,\n"
        "    ROUND(MIN(CASE WHEN pct>=0.9 THEN dep_delay END)/60.0::numeric, 2) AS p90_min,\n"
        "    COUNT(*) AS samples\n"
        f"FROM ranked WHERE {outer_where}\n"
        f"GROUP BY route_code, service_type, scheduled_time ORDER BY avg_min {order}",
        agency_id,
        *outer_params_vals,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_by_dow
# ---------------------------------------------------------------------------

# Emit ISODOW int matching agg_route_dow.dow (post migration 0011).
# Callers render via pipeline/query/formatter._dow_label.
_DOW_CASE = "EXTRACT(ISODOW FROM date::date)::smallint"


async def _exec_by_dow(intent: dict, conn, agency_id: int) -> list:
    """Return per-DOW delay stats for the requested route(s).

    Prefers the agg_route_dow aggregate table (post migration 0011 stores
    ISODOW ints). Falls back to a live deduped CTE when aggregates are absent.
    DOW values in the returned tuples are ISODOW ints (1=Mon..7=Sun); rollup
    labels ('平日', '週末') are literal strings injected by the SQL.
    """
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []

    service = intent.get("service")
    dow = intent.get("dow")
    dow_group = intent.get("dow_group")
    order = _order_keyword(intent.get("sort_order"), default="DESC")

    route_cond, route_vals, n = _route_in_clause(route_codes, 2)

    if await _agg_loaded(conn, agency_id):
        conds = ["agency_id=$1", route_cond]
        params: list = [agency_id] + route_vals
        n_agg = 2 + len(route_vals)

        if service:
            conds.append(f"service_type=${n_agg}")
            params.append(service)
            n_agg += 1

        if dow:
            dow_iso = _DOW_JP_TO_ISO.get(dow)
            if dow_iso is None:
                return []
            conds.append(f"dow=${n_agg}")
            params.append(dow_iso)
            where_sql = " AND ".join(conds)
            rows = await conn.fetch(
                "SELECT route_code, service_type, dow, avg_min, samples "
                f"FROM agg_route_dow WHERE {where_sql} ORDER BY avg_min {order}",
                *params,
            )
            return [tuple(r) for r in rows]

        group_dows = _dow_group_values(dow_group)
        if group_dows:
            phs = ", ".join(f"${n_agg + i}" for i in range(len(group_dows)))
            conds.append(f"dow IN ({phs})")
            where_sql = " AND ".join(conds)
            label = "週末" if dow_group == "weekend" else "平日"
            # label is injected as a literal in SELECT; params for WHERE only
            rows = await conn.fetch(
                f"SELECT route_code, service_type, '{label}' AS dow, "
                "ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2) AS avg_min, "
                "SUM(samples) AS samples "
                f"FROM agg_route_dow WHERE {where_sql} "
                f"GROUP BY route_code, service_type ORDER BY avg_min {order}",
                *params,
                *group_dows,
            )
            return [tuple(r) for r in rows]

        where_sql = " AND ".join(conds)
        rows = await conn.fetch(
            "SELECT route_code, service_type, dow, avg_min, samples "
            f"FROM agg_route_dow WHERE {where_sql} ORDER BY avg_min {order}",
            *params,
        )
        return [tuple(r) for r in rows]

    # raw fallback — _DEDUP_INNER uses $1=agency_id, outer params start at $2
    outer_conds = [route_cond]
    outer_vals = route_vals[:]
    outer_n = 2 + len(route_vals)

    if service:
        outer_conds.append(f"service_type=${outer_n}")
        outer_vals.append(service)
        outer_n += 1

    if dow:
        dow_iso = _DOW_JP_TO_ISO.get(dow)
        if dow_iso is None:
            return []
        outer_conds.append(f"EXTRACT(ISODOW FROM date::date)::smallint = ${outer_n}")
        outer_vals.append(dow_iso)
        outer_n += 1
        where_sql = " AND ".join(outer_conds)
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            f"SELECT route_code, service_type, {_DOW_CASE} AS dow, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            f"FROM deduped WHERE {where_sql} "
            f"GROUP BY route_code, service_type, EXTRACT(ISODOW FROM date::date) "
            f"ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order}",
            agency_id,
            *outer_vals,
        )
        return [tuple(r) for r in rows]

    if dow_group:
        label = "週末" if dow_group == "weekend" else "平日"
        dow_nums = _dow_group_values(dow_group)  # ISODOW ints
        phs = ", ".join(f"${outer_n + i}" for i in range(len(dow_nums)))
        where_sql = " AND ".join(outer_conds + [f"EXTRACT(ISODOW FROM date::date)::smallint IN ({phs})"])
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            f"SELECT route_code, service_type, '{label}' AS dow, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            f"FROM deduped WHERE {where_sql} "
            f"GROUP BY route_code, service_type ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order}",
            agency_id,
            *outer_vals,
            *dow_nums,
        )
        return [tuple(r) for r in rows]

    where_sql = " AND ".join(outer_conds)
    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}) "
        f"SELECT route_code, service_type, {_DOW_CASE} AS dow, "
        "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
        f"FROM deduped WHERE {where_sql} "
        f"GROUP BY route_code, service_type, EXTRACT(ISODOW FROM date::date) "
        f"ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order}",
        agency_id,
        *outer_vals,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_by_stop
# ---------------------------------------------------------------------------


async def _exec_by_stop(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []

    stop_name = intent.get("stop_name")
    limit = intent.get("limit", 15)
    order = _order_keyword(intent.get("sort_order"), default="DESC")

    route_cond, route_vals, n = _route_in_clause(route_codes, 2)

    if await _agg_loaded(conn, agency_id):
        conds = ["agency_id=$1", route_cond]
        params: list = [agency_id] + route_vals
        cur_n = 2 + len(route_vals)

        if stop_name:
            conds.append(f"stop_name LIKE ${cur_n}")
            params.append(f"%{stop_name}%")
            cur_n += 1

        where_sql = " AND ".join(conds)
        limit_ph = f"${cur_n}"
        params.append(limit)

        if len(route_codes) == 1:
            rows = await conn.fetch(
                "SELECT stop_sequence, stop_name, avg_min, samples "
                f"FROM agg_stop_seq WHERE {where_sql} ORDER BY avg_min {order} LIMIT {limit_ph}",
                *params,
            )
        else:
            rows = await conn.fetch(
                "SELECT stop_sequence, stop_name, "
                "ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2) AS avg_min, "
                "SUM(samples) AS samples "
                f"FROM agg_stop_seq WHERE {where_sql} "
                f"GROUP BY stop_sequence, stop_name ORDER BY avg_min {order} LIMIT {limit_ph}",
                *params,
            )
        return [tuple(r) for r in rows]

    if await _static_loaded(conn, agency_id):
        # outer params: agency_id=$1 in CTE, route params start at $2
        outer_conds = [f"d.{route_cond}", "d.stop_sequence IS NOT NULL"]
        outer_vals = route_vals[:]
        cur_n = 2 + len(route_vals)

        if stop_name:
            outer_conds.append(f"ss.stop_name LIKE ${cur_n}")
            outer_vals.append(f"%{stop_name}%")
            cur_n += 1

        where_sql = " AND ".join(outer_conds)
        limit_ph = f"${cur_n}"
        outer_vals.append(limit)

        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT d.stop_sequence, "
            "COALESCE(MAX(ss.stop_name), CAST(d.stop_sequence AS TEXT) || '番停留所'), "
            "ROUND(AVG(d.dep_delay)/60.0::numeric, 2), COUNT(*) "
            "FROM deduped d "
            "LEFT JOIN static_stop_times sst ON d.trip_id=sst.trip_id AND d.stop_sequence=sst.stop_sequence AND sst.agency_id=$1 "  # noqa: E501
            "LEFT JOIN static_stops ss ON sst.stop_id=ss.stop_id AND ss.agency_id=$1 "
            f"WHERE {where_sql} "
            f"GROUP BY d.stop_sequence HAVING COUNT(*) > 3 ORDER BY ROUND(AVG(d.dep_delay)/60.0::numeric, 2) {order} LIMIT {limit_ph}",  # noqa: E501
            agency_id,
            *outer_vals,
        )
        return [tuple(r) for r in rows]

    # bare fallback
    limit_ph = f"${n}"
    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}) "
        "SELECT stop_sequence, CAST(stop_sequence AS TEXT) || '番停留所', "
        "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
        f"FROM deduped WHERE {route_cond} AND stop_sequence IS NOT NULL "
        f"GROUP BY stop_sequence HAVING COUNT(*) > 3 ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order} LIMIT {limit_ph}",  # noqa: E501
        agency_id,
        *route_vals,
        limit,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_by_date
# ---------------------------------------------------------------------------


async def _exec_by_date(intent: dict, conn, agency_id: int) -> list:
    date_value = intent.get("date")
    if not date_value:
        return []

    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    service = intent.get("service")
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    # $1=agency_id, $2=date, then optional route/service params
    conds = ["agency_id=$1", "date=$2"]
    params: list = [agency_id, date_value]
    n = 3

    if route_codes:
        route_cond, route_vals, n = _route_in_clause(route_codes, n)
        conds.append(route_cond)
        params.extend(route_vals)

    if service:
        conds.append(f"service_type=${n}")
        params.append(service)
        n += 1

    where_sql = " AND ".join(conds)

    if await _agg_loaded(conn, agency_id):
        rows = await conn.fetch(
            "SELECT route_code, service_type, avg_min, samples "
            f"FROM agg_daily_trend WHERE {where_sql} ORDER BY avg_min {sort_order} LIMIT 20",
            *params,
        )
        return [tuple(r) for r in rows]

    # raw fallback — CTE uses $1=agency_id; outer uses date=$2, route from $3+
    outer_conds = ["date=$2"]
    outer_vals: list = [date_value]
    outer_n = 3

    if route_codes:
        route_cond, route_vals, outer_n = _route_in_clause(route_codes, outer_n)
        outer_conds.append(route_cond)
        outer_vals.extend(route_vals)

    if service:
        outer_conds.append(f"service_type=${outer_n}")
        outer_vals.append(service)

    outer_where = " AND ".join(outer_conds)
    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}) "
        "SELECT route_code, service_type, ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
        f"FROM deduped WHERE {outer_where} "
        f"GROUP BY route_code, service_type ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {sort_order} LIMIT 20",
        agency_id,
        *outer_vals,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_trend
# ---------------------------------------------------------------------------


async def _exec_trend(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    service = intent.get("service")
    trend_direction = intent.get("trend_direction", "any")
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    # Build optional filters — $1=agency_id is reused, route params start at $2
    extra_cond = ""
    extra_params: list = []
    n = 2
    if route_codes:
        route_cond, route_vals, n = _route_in_clause(route_codes, n)
        extra_cond += f" AND {route_cond}"
        extra_params.extend(route_vals)
    if service:
        extra_cond += f" AND service_type=${n}"
        extra_params.append(service)
        n += 1

    direction_where = ""
    if trend_direction == "up":
        direction_where = "WHERE delta > 0"
    elif trend_direction == "down":
        direction_where = "WHERE delta < 0"

    if not await _agg_loaded(conn, agency_id):
        return []

    rows = await conn.fetch(
        "WITH max_d AS (SELECT MAX(date::date) AS d FROM agg_daily_trend WHERE agency_id=$1),\n"
        "recent AS (\n"
        "    SELECT route_code, service_type, AVG(avg_min) AS r_avg, COUNT(*) AS n\n"
        "    FROM agg_daily_trend, max_d\n"
        f"    WHERE agency_id=$1 AND date::date > max_d.d - 14{extra_cond}\n"
        "    GROUP BY route_code, service_type HAVING COUNT(*) >= 3\n"
        "),\n"
        "older AS (\n"
        "    SELECT route_code, service_type, AVG(avg_min) AS o_avg, COUNT(*) AS n\n"
        "    FROM agg_daily_trend, max_d\n"
        f"    WHERE agency_id=$1 AND date::date BETWEEN max_d.d - 28 AND max_d.d - 14{extra_cond}\n"
        "    GROUP BY route_code, service_type HAVING COUNT(*) >= 3\n"
        "),\n"
        "joined AS (\n"
        "    SELECT r.route_code, r.service_type,\n"
        "           ROUND(r.r_avg::numeric, 2) AS r_avg,\n"
        "           ROUND(o.o_avg::numeric, 2) AS o_avg,\n"
        "           ROUND((r.r_avg - o.o_avg)::numeric, 2) AS delta\n"
        "    FROM recent r\n"
        "    JOIN older o ON r.route_code=o.route_code AND r.service_type=o.service_type\n"
        ")\n"
        f"SELECT route_code, service_type, r_avg, o_avg, delta FROM joined\n"
        f"{direction_where} ORDER BY ABS(delta) {sort_order} LIMIT 10",
        agency_id,
        *extra_params,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_on_time
# ---------------------------------------------------------------------------


async def _exec_on_time(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    service = intent.get("service")
    limit = intent.get("limit", 15)
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    if route_codes:
        route_cond, route_vals, n = _route_in_clause(route_codes, 2)
        params: list = [agency_id] + route_vals
        cond = f"agency_id=$1 AND {route_cond}"
        if service:
            cond += f" AND service_type=${n}"
            params.append(service)
            n += 1

        if await _agg_loaded(conn, agency_id):
            rows = await conn.fetch(
                "SELECT route_code, service_type, avg_min, on_time_pct, late5_pct, samples "
                f"FROM agg_route_stats WHERE {cond}",
                *params,
            )
            return [tuple(r) for r in rows]

        # raw fallback — outer uses route_cond from $2
        outer_cond = route_cond
        outer_params = route_vals[:]
        outer_n = 2 + len(route_vals)
        if service:
            outer_cond += f" AND service_type=${outer_n}"
            outer_params.append(service)
            outer_n += 1
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, ROUND(AVG(dep_delay)/60.0::numeric, 2), "
            "ROUND(SUM(CASE WHEN dep_delay<=60 THEN 1.0 ELSE 0 END)*100.0/COUNT(*)::numeric, 1), "
            "ROUND(SUM(CASE WHEN dep_delay>300 THEN 1.0 ELSE 0 END)*100.0/COUNT(*)::numeric, 1), COUNT(*) "
            f"FROM deduped WHERE {outer_cond} GROUP BY route_code, service_type",
            agency_id,
            *outer_params,
        )
        return [tuple(r) for r in rows]

    # no route specified — ranking mode
    if await _agg_loaded(conn, agency_id):
        if service:
            rows = await conn.fetch(
                "SELECT route_code, service_type, on_time_pct, avg_min, samples "
                f"FROM agg_route_stats WHERE agency_id=$1 AND service_type=$2 ORDER BY on_time_pct {sort_order} LIMIT $3",  # noqa: E501
                agency_id,
                service,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT route_code, service_type, on_time_pct, avg_min, samples "
                f"FROM agg_route_stats WHERE agency_id=$1 ORDER BY on_time_pct {sort_order} LIMIT $2",
                agency_id,
                limit,
            )
        return [tuple(r) for r in rows]

    # raw fallback ranking
    if service:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, "
            "ROUND(SUM(CASE WHEN dep_delay<=60 THEN 1.0 ELSE 0 END)*100.0/COUNT(*)::numeric, 1) AS on_time_pct, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            "FROM deduped WHERE service_type=$2 GROUP BY route_code, service_type HAVING COUNT(*) > 10 "
            f"ORDER BY on_time_pct {sort_order} LIMIT $3",
            agency_id,
            service,
            limit,
        )
    else:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, "
            "ROUND(SUM(CASE WHEN dep_delay<=60 THEN 1.0 ELSE 0 END)*100.0/COUNT(*)::numeric, 1) AS on_time_pct, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            "FROM deduped GROUP BY route_code, service_type HAVING COUNT(*) > 10 "
            f"ORDER BY on_time_pct {sort_order} LIMIT $2",
            agency_id,
            limit,
        )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_compare
# ---------------------------------------------------------------------------


async def _exec_compare(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []

    route_cond, route_vals, n = _route_in_clause(route_codes, 2)
    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}) "
        "SELECT service_type, ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
        f"FROM deduped WHERE {route_cond} GROUP BY service_type",
        agency_id,
        *route_vals,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_worst_5min
# ---------------------------------------------------------------------------


async def _exec_worst_5min(intent: dict, conn, agency_id: int) -> list:
    service = intent.get("service")
    limit = intent.get("limit", 15)
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    if await _agg_loaded(conn, agency_id):
        if service:
            rows = await conn.fetch(
                "SELECT route_code, service_type, avg_min, late_5min_plus, samples "
                "FROM agg_route_stats WHERE agency_id=$1 AND late_5min_plus > 0 AND service_type=$2 "
                f"ORDER BY late_5min_plus {sort_order} LIMIT $3",
                agency_id,
                service,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT route_code, service_type, avg_min, late_5min_plus, samples "
                "FROM agg_route_stats WHERE agency_id=$1 AND late_5min_plus > 0 "
                f"ORDER BY late_5min_plus {sort_order} LIMIT $2",
                agency_id,
                limit,
            )
        return [tuple(r) for r in rows]

    if service:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, ROUND(AVG(dep_delay)/60.0::numeric, 2), "
            "SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END), COUNT(*) "
            "FROM deduped WHERE service_type=$2 GROUP BY route_code, service_type "
            "HAVING SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) > 0 "
            f"ORDER BY SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) {sort_order} LIMIT $3",
            agency_id,
            service,
            limit,
        )
    else:
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, ROUND(AVG(dep_delay)/60.0::numeric, 2), "
            "SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END), COUNT(*) "
            "FROM deduped GROUP BY route_code, service_type "
            "HAVING SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) > 0 "
            f"ORDER BY SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) {sort_order} LIMIT $2",
            agency_id,
            limit,
        )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_stop_ranking
# ---------------------------------------------------------------------------


async def _exec_stop_ranking(intent: dict, conn, agency_id: int) -> list:
    limit = intent.get("limit", 15)
    stop_name = intent.get("stop_name")
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    if await _agg_loaded(conn, agency_id):
        if stop_name:
            rows = await conn.fetch(
                "SELECT route_code, stop_sequence, stop_name, avg_min, samples "
                "FROM agg_stop_seq WHERE agency_id=$1 AND stop_name LIKE $2 "
                f"ORDER BY avg_min {sort_order} LIMIT $3",
                agency_id,
                f"%{stop_name}%",
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT route_code, stop_sequence, stop_name, avg_min, samples "
                f"FROM agg_stop_seq WHERE agency_id=$1 ORDER BY avg_min {sort_order} LIMIT $2",
                agency_id,
                limit,
            )
        return [tuple(r) for r in rows]

    if await _static_loaded(conn, agency_id):
        if stop_name:
            rows = await conn.fetch(
                f"WITH deduped AS ({_DEDUP_INNER}) "
                "SELECT d.route_code, d.stop_sequence, "
                "COALESCE(MAX(ss.stop_name), CAST(d.stop_sequence AS TEXT) || '番停留所'), "
                "ROUND(AVG(d.dep_delay)/60.0::numeric, 2), COUNT(*) "
                "FROM deduped d "
                "LEFT JOIN static_stop_times sst ON d.trip_id=sst.trip_id AND d.stop_sequence=sst.stop_sequence AND sst.agency_id=$1 "  # noqa: E501
                "LEFT JOIN static_stops ss ON sst.stop_id=ss.stop_id AND ss.agency_id=$1 "
                "WHERE d.stop_sequence IS NOT NULL AND ss.stop_name LIKE $2 "
                "GROUP BY d.route_code, d.stop_sequence HAVING COUNT(*) > 10 "
                f"ORDER BY ROUND(AVG(d.dep_delay)/60.0::numeric, 2) {sort_order} LIMIT $3",
                agency_id,
                f"%{stop_name}%",
                limit,
            )
        else:
            rows = await conn.fetch(
                f"WITH deduped AS ({_DEDUP_INNER}) "
                "SELECT d.route_code, d.stop_sequence, "
                "COALESCE(MAX(ss.stop_name), CAST(d.stop_sequence AS TEXT) || '番停留所'), "
                "ROUND(AVG(d.dep_delay)/60.0::numeric, 2), COUNT(*) "
                "FROM deduped d "
                "LEFT JOIN static_stop_times sst ON d.trip_id=sst.trip_id AND d.stop_sequence=sst.stop_sequence AND sst.agency_id=$1 "  # noqa: E501
                "LEFT JOIN static_stops ss ON sst.stop_id=ss.stop_id AND ss.agency_id=$1 "
                "WHERE d.stop_sequence IS NOT NULL "
                "GROUP BY d.route_code, d.stop_sequence HAVING COUNT(*) > 10 "
                f"ORDER BY ROUND(AVG(d.dep_delay)/60.0::numeric, 2) {sort_order} LIMIT $2",
                agency_id,
                limit,
            )
        return [tuple(r) for r in rows]

    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}) "
        "SELECT route_code, stop_sequence, CAST(stop_sequence AS TEXT) || '番停留所', "
        "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
        "FROM deduped WHERE stop_sequence IS NOT NULL "
        "GROUP BY route_code, stop_sequence HAVING COUNT(*) > 10 "
        f"ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {sort_order} LIMIT $2",
        agency_id,
        limit,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_compare_ranking
# ---------------------------------------------------------------------------


async def _exec_compare_ranking(intent: dict, conn, agency_id: int) -> list:
    limit = intent.get("limit", 15)
    polarity = intent.get("compare_polarity", "any")
    sort_order = _order_keyword(intent.get("sort_order"), default="DESC")

    polarity_where = ""
    if polarity == "holiday_worse":
        polarity_where = "WHERE signed_delta > 0"
    elif polarity == "weekday_worse":
        polarity_where = "WHERE signed_delta < 0"

    if await _agg_loaded(conn, agency_id):
        rows = await conn.fetch(
            "WITH base AS ("
            "  SELECT route_code, "
            "  ROUND(MAX(CASE WHEN service_type='平日' THEN avg_min END)::numeric, 2) AS heijitsu, "
            "  ROUND(MAX(CASE WHEN service_type='土日祝' THEN avg_min END)::numeric, 2) AS kyujitsu "
            "  FROM agg_route_stats WHERE agency_id=$1 GROUP BY route_code"
            "), d AS ("
            "  SELECT route_code, heijitsu, kyujitsu, "
            "  ROUND((kyujitsu - heijitsu)::numeric, 2) AS signed_delta, "
            "  ROUND(ABS(kyujitsu - heijitsu)::numeric, 2) AS abs_delta "
            "  FROM base WHERE heijitsu IS NOT NULL AND kyujitsu IS NOT NULL"
            ") "
            "SELECT route_code, heijitsu, kyujitsu, abs_delta, signed_delta "
            f"FROM d {polarity_where} ORDER BY abs_delta {sort_order} LIMIT $2",
            agency_id,
            limit,
        )
        return [tuple(r) for r in rows]

    rows = await conn.fetch(
        f"WITH deduped AS ({_DEDUP_INNER}), "
        "base AS ("
        "  SELECT route_code, "
        "  ROUND(AVG(CASE WHEN service_type='平日' THEN dep_delay END)/60.0::numeric, 2) AS heijitsu, "
        "  ROUND(AVG(CASE WHEN service_type='土日祝' THEN dep_delay END)/60.0::numeric, 2) AS kyujitsu "
        "  FROM deduped GROUP BY route_code"
        "), d AS ("
        "  SELECT route_code, heijitsu, kyujitsu, "
        "  ROUND((kyujitsu - heijitsu)::numeric, 2) AS signed_delta, "
        "  ROUND(ABS(kyujitsu - heijitsu)::numeric, 2) AS abs_delta "
        "  FROM base WHERE heijitsu IS NOT NULL AND kyujitsu IS NOT NULL"
        ") "
        "SELECT route_code, heijitsu, kyujitsu, abs_delta, signed_delta "
        f"FROM d {polarity_where} ORDER BY abs_delta {sort_order} LIMIT $2",
        agency_id,
        limit,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_dow_ranking
# ---------------------------------------------------------------------------


async def _exec_dow_ranking(intent: dict, conn, agency_id: int) -> list:
    """Return a delay ranking filtered to a specific DOW or DOW group.

    Requires either 'dow' (Japanese char, e.g. '月') or 'dow_group'
    ('weekday'/'weekend') in intent. Returns [] when neither is set.
    DOW column in result tuples is an ISODOW int (agg path) or the rollup
    label string (dow_group path). Callers must pass through _dow_label.
    """
    dow = intent.get("dow")
    dow_group = intent.get("dow_group")
    service = intent.get("service")
    limit = intent.get("limit", 15)
    order = _order_keyword(intent.get("sort_order"), default="DESC")

    if await _agg_loaded(conn, agency_id):
        params: list = [agency_id]
        conds = ["agency_id=$1"]
        n = 2

        if service:
            conds.append(f"service_type=${n}")
            params.append(service)
            n += 1

        if dow:
            dow_iso = _DOW_JP_TO_ISO.get(dow)
            if dow_iso is None:
                return []
            conds.append(f"dow=${n}")
            params.append(dow_iso)
            n += 1
            where_sql = "WHERE " + " AND ".join(conds)
            params.append(limit)
            rows = await conn.fetch(
                "SELECT route_code, service_type, dow, avg_min, samples "
                f"FROM agg_route_dow {where_sql} ORDER BY avg_min {order} LIMIT ${n}",
                *params,
            )
            return [tuple(r) for r in rows]

        group_dows = _dow_group_values(dow_group)
        if group_dows:
            phs = ", ".join(f"${n + i}" for i in range(len(group_dows)))
            conds.append(f"dow IN ({phs})")
            where_sql = "WHERE " + " AND ".join(conds)
            label = "週末" if dow_group == "weekend" else "平日"
            params.extend(group_dows)
            n += len(group_dows)
            params.append(limit)
            rows = await conn.fetch(
                f"SELECT route_code, service_type, '{label}' AS dow, "
                "ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2) AS avg_min, "
                "SUM(samples) AS samples "
                f"FROM agg_route_dow {where_sql} GROUP BY route_code, service_type "
                f"ORDER BY avg_min {order} LIMIT ${n}",
                *params,
            )
            return [tuple(r) for r in rows]

        return []

    # raw fallback
    if dow:
        dow_iso = _DOW_JP_TO_ISO.get(dow)
        if dow_iso is None:
            return []
        params = [agency_id, dow_iso]
        n = 3
        svc_and = ""
        if service:
            svc_and = f" AND service_type=${n}"
            params.append(service)
            n += 1
        params.append(limit)
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            "SELECT route_code, service_type, "
            f"{_DOW_CASE} AS dow, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            f"FROM deduped WHERE EXTRACT(ISODOW FROM date::date)::smallint = $2{svc_and} "
            f"GROUP BY route_code, service_type HAVING COUNT(*) > 5 "
            f"ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order} LIMIT ${n}",
            *params,
        )
        return [tuple(r) for r in rows]

    if dow_group:
        dow_nums = _dow_group_values(dow_group)
        label = "週末" if dow_group == "weekend" else "平日"
        phs = ", ".join(f"${2 + i}" for i in range(len(dow_nums)))
        params = [agency_id, *dow_nums]
        n = 2 + len(dow_nums)
        svc_and = ""
        if service:
            svc_and = f" AND service_type=${n}"
            params.append(service)
            n += 1
        params.append(limit)
        rows = await conn.fetch(
            f"WITH deduped AS ({_DEDUP_INNER}) "
            f"SELECT route_code, service_type, '{label}' AS dow, "
            "ROUND(AVG(dep_delay)/60.0::numeric, 2), COUNT(*) "
            f"FROM deduped WHERE EXTRACT(ISODOW FROM date::date)::smallint IN ({phs}){svc_and} "
            "GROUP BY route_code, service_type HAVING COUNT(*) > 5 "
            f"ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) {order} LIMIT ${n}",
            *params,
        )
        return [tuple(r) for r in rows]

    return []


# ---------------------------------------------------------------------------
# _exec_stop_list
# ---------------------------------------------------------------------------


async def _exec_stop_list(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []
    route_code = route_codes[0]
    trip = await conn.fetchrow(
        "SELECT st.trip_id FROM static_trips st "
        "JOIN static_routes sr ON st.route_id = sr.route_id "
        "WHERE sr.agency_id=$1 AND st.agency_id=$1 "
        "AND sr.route_id LIKE '%(' || $2 || ')' LIMIT 1",
        agency_id,
        route_code,
    )
    if not trip:
        return []
    rows = await conn.fetch(
        "SELECT sst.stop_sequence, ss.stop_name, sst.departure_time "
        "FROM static_stop_times sst "
        "JOIN static_stops ss ON sst.stop_id = ss.stop_id AND ss.agency_id=$1 "
        "WHERE sst.agency_id=$1 AND sst.trip_id=$2 ORDER BY sst.stop_sequence",
        agency_id,
        trip["trip_id"],
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_routes_at_stop
# ---------------------------------------------------------------------------


async def _exec_routes_at_stop(intent: dict, conn, agency_id: int) -> list:
    stop_name = intent.get("stop_name")
    if not stop_name:
        return []
    rows = await conn.fetch(
        "SELECT DISTINCT sr.route_id, sr.route_short_name, ss.stop_name "
        "FROM static_stop_times sst "
        "JOIN static_stops ss ON sst.stop_id = ss.stop_id AND ss.agency_id=$1 "
        "JOIN static_trips st ON sst.trip_id = st.trip_id AND st.agency_id=$1 "
        "JOIN static_routes sr ON st.route_id = sr.route_id AND sr.agency_id=$1 "
        "WHERE sst.agency_id=$1 AND ss.stop_name LIKE $2 ORDER BY sr.route_short_name",
        agency_id,
        f"%{stop_name}%",
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_route_info
# ---------------------------------------------------------------------------


async def _exec_route_info(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []
    route_code = route_codes[0]
    rows = await conn.fetch(
        "SELECT sr.route_id, sr.route_short_name, "
        "COUNT(DISTINCT sst.stop_id) AS stop_count, "
        "MIN(sst.departure_time) AS first_dep, "
        "MAX(sst.departure_time) AS last_dep, "
        "COUNT(DISTINCT st.trip_id) AS trip_count "
        "FROM static_routes sr "
        "JOIN static_trips st ON st.route_id = sr.route_id AND st.agency_id=$1 "
        "JOIN static_stop_times sst ON sst.trip_id = st.trip_id AND sst.agency_id=$1 "
        "WHERE sr.agency_id=$1 AND sr.route_id LIKE '%(' || $2 || ')' GROUP BY sr.route_id, sr.route_short_name",
        agency_id,
        route_code,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# _exec_timetable
# ---------------------------------------------------------------------------


async def _exec_timetable(intent: dict, conn, agency_id: int) -> list:
    route_codes = await _route_codes_from_intent(intent, conn, agency_id)
    if not route_codes:
        return []
    route_code = route_codes[0]
    stop_name = intent.get("stop_name")
    time_band = intent.get("time_band")

    if stop_name:
        params: list = [agency_id, route_code, f"%{stop_name}%"]
        n = 4
        where_extra = ""
        if time_band:
            tb_cond, tb_vals, n = _time_band_clause(time_band, n, col="sst.departure_time")
            if tb_cond:
                where_extra = f" AND {tb_cond}"
                params.extend(tb_vals)
        rows = await conn.fetch(
            "SELECT sst.departure_time, st.trip_headsign, ss.stop_name "
            "FROM static_stop_times sst "
            "JOIN static_trips st ON sst.trip_id = st.trip_id AND st.agency_id=$1 "
            "JOIN static_routes sr ON st.route_id = sr.route_id AND sr.agency_id=$1 "
            "JOIN static_stops ss ON sst.stop_id = ss.stop_id AND ss.agency_id=$1 "
            "WHERE sst.agency_id=$1 AND sr.route_id LIKE '%(' || $2 || ')' AND ss.stop_name LIKE $3"
            f"{where_extra} ORDER BY sst.departure_time",
            *params,
        )
        return [tuple(r) for r in rows]

    # First stop per trip
    params = [agency_id, route_code]
    n = 3
    where_extra = ""
    if time_band:
        tb_cond, tb_vals, n = _time_band_clause(time_band, n, col="sst.departure_time")
        if tb_cond:
            where_extra = f" AND {tb_cond}"
            params.extend(tb_vals)
    rows = await conn.fetch(
        "WITH first_seq AS ("
        "  SELECT trip_id, MIN(stop_sequence) AS min_seq FROM static_stop_times WHERE agency_id=$1 GROUP BY trip_id"
        ") "
        "SELECT sst.departure_time, st.trip_headsign "
        "FROM static_stop_times sst "
        "JOIN first_seq fs ON sst.trip_id = fs.trip_id AND sst.stop_sequence = fs.min_seq "
        "JOIN static_trips st ON sst.trip_id = st.trip_id AND st.agency_id=$1 "
        "JOIN static_routes sr ON st.route_id = sr.route_id AND sr.agency_id=$1 "
        "WHERE sst.agency_id=$1 AND sr.route_id LIKE '%(' || $2 || ')'"
        f"{where_extra} ORDER BY sst.departure_time",
        *params,
    )
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# Dispatch table and execute()
# ---------------------------------------------------------------------------

_STATIC_REQUIRED = frozenset({"stop_list", "routes_at_stop", "route_info", "timetable"})

EXECUTORS = {
    "ranking": _exec_ranking,
    "by_hour": _exec_by_hour,
    "by_dow": _exec_by_dow,
    "by_stop": _exec_by_stop,
    "by_date": _exec_by_date,
    "trend": _exec_trend,
    "on_time": _exec_on_time,
    "compare": _exec_compare,
    "worst_5min": _exec_worst_5min,
    "stop_ranking": _exec_stop_ranking,
    "dow_ranking": _exec_dow_ranking,
    "compare_ranking": _exec_compare_ranking,
    "stop_list": _exec_stop_list,
    "routes_at_stop": _exec_routes_at_stop,
    "route_info": _exec_route_info,
    "timetable": _exec_timetable,
}


async def execute(intent: dict, conn, agency_id: int) -> list | None:
    """Return list[tuple], [] for no data, None when static is required but absent."""
    if intent.get("unknown") or intent["query_type"] not in EXECUTORS:
        return []
    if intent["query_type"] in _STATIC_REQUIRED and not await _static_loaded(conn, agency_id):
        return None
    return await EXECUTORS[intent["query_type"]](intent, conn, agency_id)
