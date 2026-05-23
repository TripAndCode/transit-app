"""Render report query-result tuples into Japanese-language strings.

Consumed by api/routers/reports.py for the /api/{agency_id}/reports/{type}
endpoint's text body. Each `_fmt_*` function matches one query_type from
pipeline.reports.compute_*; FORMATTERS is the dispatch table.

DOW columns arriving from compute_dow_ranking are ISODOW ints
(1=Mon..7=Sun, per migration 0011). They are rendered via
pipeline.query.labels.dow_label; rollup labels ('平日', '週末') and
legacy Japanese-char input pass through unchanged.
"""

from pipeline.query.labels import dow_label


def _r(x, d: int = 1) -> str:
    """Round a numeric DB value to *d* decimal places and return as a string.

    Returns '—' for None/NULL values.
    """
    if x is None:
        return "—"
    return f"{round(float(x), d):.{d}f}"


def _no_data(label: str = "") -> str:
    """Default empty-result string for any FORMATTERS branch."""
    return f"{label}データがありません。期間や系統フィルタを見直してください。"


def _route_scope_label(intent: dict) -> str:
    """Render the leading '系統N' prefix from the intent's route field."""
    route = intent.get("route") or intent.get("route_name")
    return f"系統{route}" if route else ""


_NO_STATIC_MSG = (
    "この質問にはGTFS Staticデータが必要です。\n"
    "`python gtfs_rag_pipeline.py load_static ./raw_archives_static` を先に実行してください。"
)


def _fmt_ranking(rows: list, intent: dict) -> str:
    """Render a ranking report (worst-delay routes) into Japanese text.

    Row shape from ``compute_ranking`` is
    ``(route_code, service_type, avg_min, p50_min, p90_min, samples)``.
    """
    service = intent.get("service")
    label = f"{service}の" if service else ""
    # Both branches render avg / p50 / p90 / samples in that order. The
    # pre-trim no-service branch mis-labelled p50/p90 as 平日/土日祝 — fixed
    # because that branch is the always-used /reports/ranking path.
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）平均{_r(r[2])}分、p50={_r(r[3])}分、p90={_r(r[4])}分（{r[5]}件）"
        for i, r in enumerate(rows, 1)
    ]
    return f"【{label}遅延ランキング上位{intent.get('limit', 15)}系統】\n" + "\n".join(lines)


def _fmt_on_time(rows: list, intent: dict) -> str:
    """Render the on-time-rate ranking into Japanese text.

    Row shape from ``compute_on_time`` is
    ``(route_code, service_type, on_time_pct, avg_min, samples)``.
    """
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）定時率{_r(r[2], 1)}%、平均{_r(r[3])}分（{r[4]}件）" for i, r in enumerate(rows, 1)
    ]
    return f"【{label}定時率ランキング】\n" + "\n".join(lines)


def _fmt_worst_5min(rows: list, intent: dict) -> str:
    """Render the >5min-delay-count ranking into Japanese text.

    Row shape from ``compute_worst_5min`` is
    ``(route_code, service_type, late5_count, avg_min, samples)``.
    """
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [f"{i}位: 系統{r[0]}（{r[1]}）5分超: {r[2]}回、平均{_r(r[3])}分（{r[4]}件）" for i, r in enumerate(rows, 1)]
    return f"【{label}5分超遅延ランキング】\n" + "\n".join(lines)


def _fmt_dow_ranking(rows: list, intent: dict) -> str:
    """Render a DOW-filtered delay ranking.

    Each row is (route_code, service_type, dow, avg_min, samples). `dow`
    is an ISODOW int (1..7) or a rollup label string; dow_label handles
    both shapes.
    """
    dow = intent.get("dow", "")
    dow_group = intent.get("dow_group")
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）{dow_label(r[2])}: 平均{_r(r[3])}分（{r[4]}件）" for i, r in enumerate(rows, 1)
    ]
    if dow_group == "weekend":
        header = f"【{label}週末遅延ランキング】"
    elif dow_group == "weekday":
        header = f"【{label}平日遅延ランキング】"
    else:
        header = f"【{label}{dow}曜日遅延ランキング】"
    return f"{header}\n" + "\n".join(lines)


def _fmt_compare_ranking(rows: list, intent: dict) -> str:
    """Render the weekday-vs-weekend delta ranking into Japanese text."""
    lines = []
    for i, r in enumerate(rows, 1):
        signed = float(r[4]) if len(r) >= 5 else (float(r[2] or 0) - float(r[1] or 0))
        direction = "土日祝>平日" if signed > 0 else ("平日>土日祝" if signed < 0 else "同程度")
        lines.append(f"{i}位: 系統{r[0]} 平日{_r(r[1])}分 / 土日祝{_r(r[2])}分（差: {_r(r[3])}分, {direction}）")
    return "【平日・土日祝 遅延差ランキング】\n" + "\n".join(lines)


FORMATTERS = {
    "ranking": _fmt_ranking,
    "on_time": _fmt_on_time,
    "worst_5min": _fmt_worst_5min,
    "compare_ranking": _fmt_compare_ranking,
    "dow_ranking": _fmt_dow_ranking,
}


def format_result(query_type: str, rows, intent: dict) -> str:
    """Public entry point: dispatch a result tuple list to its renderer.

    `rows is None` signals the caller had no static GTFS data loaded.
    Unknown `query_type` and empty `rows` both fall back to _no_data().
    """
    if rows is None:
        return _NO_STATIC_MSG
    fmt = FORMATTERS.get(query_type)
    if not fmt:
        return _no_data()
    if not rows:
        return _no_data()
    return fmt(rows, intent)
