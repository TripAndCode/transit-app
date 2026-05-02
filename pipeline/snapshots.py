import logging

from pipeline.query.formatter import format_result

_log = logging.getLogger(__name__)

_SNAPSHOT_DEFS = [
    (
        "ranking",
        "ranking",
        {"limit": 100},
        """
        SELECT route_code, '全日' AS service_type,
            SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min,
            MAX(CASE WHEN service_type = '平日'  THEN avg_min END) AS heijitsu_avg,
            MAX(CASE WHEN service_type = '土日祝' THEN avg_min END) AS kyujitsu_avg,
            SUM(samples) AS samples
        FROM agg_route_stats WHERE agency_id=%(agency_id)s
        GROUP BY route_code
        ORDER BY SUM(avg_min * samples) / NULLIF(SUM(samples), 0) DESC NULLS LAST
        LIMIT 100
        """,
    ),
    (
        "ranking_best",
        "ranking",
        {"limit": 100, "sort_order": "asc"},
        """
        SELECT route_code, '全日' AS service_type,
            SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min,
            MAX(CASE WHEN service_type = '平日'  THEN avg_min END) AS heijitsu_avg,
            MAX(CASE WHEN service_type = '土日祝' THEN avg_min END) AS kyujitsu_avg,
            SUM(samples) AS samples
        FROM agg_route_stats WHERE agency_id=%(agency_id)s
        GROUP BY route_code
        ORDER BY SUM(avg_min * samples) / NULLIF(SUM(samples), 0) ASC NULLS LAST
        LIMIT 100
        """,
    ),
    (
        "on_time",
        "on_time",
        {"limit": 100},
        """
        SELECT route_code, service_type, on_time_pct, avg_min, samples
        FROM agg_route_stats WHERE agency_id=%(agency_id)s
        ORDER BY on_time_pct DESC NULLS LAST LIMIT 100
        """,
    ),
    (
        "worst_5min",
        "worst_5min",
        {"limit": 100},
        """
        SELECT route_code, service_type, avg_min, late_5min_plus, samples
        FROM agg_route_stats WHERE agency_id=%(agency_id)s AND late_5min_plus > 0
        ORDER BY late_5min_plus DESC LIMIT 100
        """,
    ),
    (
        "trend",
        "trend",
        {},
        """
        WITH max_d AS (
            SELECT MAX(date) AS d FROM agg_daily_trend WHERE agency_id=%(agency_id)s
        ),
        recent AS (
            SELECT route_code, service_type, AVG(avg_min) AS r_avg
            FROM agg_daily_trend, max_d
            WHERE agency_id=%(agency_id)s
              AND date::date > max_d.d::date - '14 days'::interval
            GROUP BY route_code, service_type HAVING COUNT(*) >= 3
        ),
        older AS (
            SELECT route_code, service_type, AVG(avg_min) AS o_avg
            FROM agg_daily_trend, max_d
            WHERE agency_id=%(agency_id)s
              AND date::date BETWEEN max_d.d::date - '28 days'::interval
                                 AND max_d.d::date - '14 days'::interval
            GROUP BY route_code, service_type HAVING COUNT(*) >= 3
        )
        SELECT r.route_code, r.service_type,
               ROUND(r.r_avg::numeric, 2) AS r_avg,
               ROUND(o.o_avg::numeric, 2) AS o_avg,
               ROUND((r.r_avg - o.o_avg)::numeric, 2) AS delta
        FROM recent r
        JOIN older o ON r.route_code = o.route_code AND r.service_type = o.service_type
        ORDER BY ABS(r.r_avg - o.o_avg) DESC LIMIT 30
        """,
    ),
    (
        "compare_ranking",
        "compare_ranking",
        {"limit": 100},
        """
        WITH base AS (
            SELECT route_code,
                ROUND(MAX(CASE WHEN service_type = '平日'  THEN avg_min END)::numeric, 2) AS heijitsu,
                ROUND(MAX(CASE WHEN service_type = '土日祝' THEN avg_min END)::numeric, 2) AS kyujitsu
            FROM agg_route_stats WHERE agency_id=%(agency_id)s GROUP BY route_code
        ), d AS (
            SELECT route_code, heijitsu, kyujitsu,
                ROUND((kyujitsu - heijitsu)::numeric, 2) AS signed_delta,
                ROUND(ABS(kyujitsu - heijitsu)::numeric, 2) AS abs_delta
            FROM base WHERE heijitsu IS NOT NULL AND kyujitsu IS NOT NULL
        )
        SELECT route_code, heijitsu, kyujitsu, abs_delta, signed_delta
        FROM d ORDER BY abs_delta DESC LIMIT 100
        """,
    ),
    (
        "dow_weekend",
        "dow_ranking",
        {"dow_group": "weekend", "limit": 100},
        """
        SELECT route_code, service_type, '週末' AS dow,
            ROUND(SUM(avg_min * samples) / NULLIF(SUM(samples), 0)::numeric, 2) AS avg_min,
            SUM(samples) AS samples
        FROM agg_route_dow WHERE agency_id=%(agency_id)s AND dow IN ('土', '日')
        GROUP BY route_code, service_type
        ORDER BY avg_min DESC NULLS LAST LIMIT 100
        """,
    ),
    (
        "dow_weekday",
        "dow_ranking",
        {"dow_group": "weekday", "limit": 100},
        """
        SELECT route_code, service_type, '平日' AS dow,
            ROUND(SUM(avg_min * samples) / NULLIF(SUM(samples), 0)::numeric, 2) AS avg_min,
            SUM(samples) AS samples
        FROM agg_route_dow WHERE agency_id=%(agency_id)s AND dow IN ('月', '火', '水', '木', '金')
        GROUP BY route_code, service_type
        ORDER BY avg_min DESC NULLS LAST LIMIT 100
        """,
    ),
]

_UPSERT_SQL = (
    "INSERT INTO snapshots (agency_id, report_type, rendered_at, text) "
    "VALUES (%s, %s, NOW(), %s) "
    "ON CONFLICT (agency_id, report_type) DO UPDATE "
    "SET text = EXCLUDED.text, rendered_at = EXCLUDED.rendered_at"
)


def write_snapshots(agency_id: int, conn) -> None:
    written = 0
    for report_type, query_type, intent, sql in _SNAPSHOT_DEFS:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, {"agency_id": agency_id})
                rows = cur.fetchall()
            if not rows:
                _log.info("snapshot %s: no rows, skipping", report_type)
                continue
            text = format_result(query_type, rows, intent)
            with conn.cursor() as cur:
                cur.execute(_UPSERT_SQL, (agency_id, report_type, text))
            conn.commit()
            written += 1
            _log.info("snapshot %s: %d rows", report_type, len(rows))
        except Exception as exc:
            _log.warning("snapshot %s failed: %s", report_type, exc)
            conn.rollback()
    _log.info("snapshots: %d/%d written", written, len(_SNAPSHOT_DEFS))
