"""Render report query-result tuples into locale-appropriate strings.

Consumed by api/routers/reports.py for the /api/{agency_id}/reports/{type}
endpoint's text body. Each ``_fmt_*`` function matches one query_type from
``pipeline.reports.compute_*``; :data:`FORMATTERS` is the dispatch table.

DOW columns arriving from compute_dow_ranking are ISODOW ints
(1=Mon..7=Sun, per migration 0011). They render via
``pipeline.query.labels.dow_label`` which itself honours the locale;
rollup labels ('平日', '週末') get translated for ``en`` callers.

Strings live in :data:`_LOCALES` keyed on ``(template, locale)`` so a new
language only needs to add a column rather than rewriting handler code.
"""

from typing import Any

from pipeline.query.labels import dow_label
from pipeline.reports.rankings import _round1, _weighted_avg_min

_LOCALES: dict[tuple[str, str], str] = {
    ("no_data", "ja"): "データがありません。期間や路線フィルタを見直してください。",
    ("no_data", "en"): "No data available. Try widening the range or clearing route filters.",
    ("no_static", "ja"): (
        "この質問にはGTFS Staticデータが必要です。\n"
        "`python gtfs_pipeline.py load_static ./raw_archives_static` を先に実行してください。"
    ),
    ("no_static", "en"): (
        "This report needs GTFS static data.\nRun `python gtfs_pipeline.py load_static ./raw_archives_static` first."
    ),
    ("ranking_header", "ja"): "【{label}遅延ランキング上位{limit}路線】",
    ("ranking_header", "en"): "[{label}Delay ranking — top {limit} routes]",
    ("ranking_row", "ja"): "{rank}位: 路線{route}（{service}）平均{avg}分、p50={p50}分、p90={p90}分（{samples}件）",
    ("ranking_row", "en"): (
        "#{rank} route {route} ({service}) mean {avg} min, p50={p50} min, p90={p90} min ({samples} samples)"
    ),
    ("on_time_header", "ja"): "【{label}定時率ランキング】",
    ("on_time_header", "en"): "[{label}On-time rate ranking]",
    ("on_time_row", "ja"): "{rank}位: 路線{route}（{service}）定時率{pct}%、平均{avg}分（{samples}件）",
    ("on_time_row", "en"): "#{rank} route {route} ({service}) on-time {pct}%, mean {avg} min ({samples} samples)",
    ("worst5_header", "ja"): "【{label}5分超遅延ランキング】",
    ("worst5_header", "en"): "[{label}5+ minute delay ranking]",
    ("worst5_row", "ja"): "{rank}位: 路線{route}（{service}）5分超 {count}回、平均{avg}分（{samples}件）",
    ("worst5_row", "en"): "#{rank} route {route} ({service}) 5+ min: {count} times, mean {avg} min ({samples} samples)",
    ("dow_header_weekend", "ja"): "【{label}週末遅延ランキング】",
    ("dow_header_weekend", "en"): "[{label}Weekend delay ranking]",
    ("dow_header_weekday", "ja"): "【{label}平日遅延ランキング】",
    ("dow_header_weekday", "en"): "[{label}Weekday delay ranking]",
    ("dow_header_other", "ja"): "【{label}{dow}曜日遅延ランキング】",
    ("dow_header_other", "en"): "[{label}{dow} delay ranking]",
    ("dow_row", "ja"): "{rank}位: 路線{route}（{service}）{dow_label}：平均{avg}分（{samples}件）",
    ("dow_row", "en"): "#{rank} route {route} ({service}) {dow_label}: mean {avg} min ({samples} samples)",
    ("compare_header", "ja"): "【平日・土日祝 遅延差ランキング】",
    ("compare_header", "en"): "[Weekday vs weekend/holiday delay-delta ranking]",
    ("compare_row", "ja"): "{rank}位: 路線{route} 平日{weekday}分 / 土日祝{weekend}分（差：{delta}分、{direction}）",
    ("compare_row", "en"): (
        "#{rank} route {route} weekday {weekday} min / weekend {weekend} min (Δ {delta} min, {direction})"
    ),
    ("dir_weekend_gt", "ja"): "土日祝>平日",
    ("dir_weekend_gt", "en"): "weekend>weekday",
    ("dir_weekday_gt", "ja"): "平日>土日祝",
    ("dir_weekday_gt", "en"): "weekday>weekend",
    ("dir_equal", "ja"): "同程度",
    ("dir_equal", "en"): "similar",
    ("service_suffix", "ja"): "{service}の",
    ("service_suffix", "en"): "{service} ",
    ("trend_header", "ja"): "【日次トレンド({from_date} 〜 {to_date})】\n平均: {avg:.2f}分 / 観測日数: {days}日",
    ("trend_header", "en"): "[Daily trend ({from_date} to {to_date})]\nmean: {avg:.2f} min / observed days: {days}",
    ("trend_empty", "ja"): "選択した期間にデータがありません。",
    ("trend_empty", "en"): "No data in the selected period.",
}


def _t(template: str, locale: str, **vars: Any) -> str:
    """Resolve a localised template, JP fallback. See pipeline.query.tools._summary."""
    if locale not in ("ja", "en"):
        locale = "ja"
    tpl = _LOCALES.get((template, locale)) or _LOCALES.get((template, "ja"), template)
    try:
        return tpl.format(**vars) if vars else tpl
    except KeyError:
        return tpl


def _r(x) -> str:
    """Round a numeric DB value to 1 decimal place and return as a string.

    Delegates to ``pipeline.reports.rankings``'s ``_round1``, which matches
    Postgres's ``ROUND()`` (half away from zero) rather than plain Python
    ``round()`` (round-half-to-even). The row values rendered here (e.g.
    ``avg_min``) already arrive rounded to 2dp by that same convention;
    re-rounding them with a different rounding rule could land on a
    different digit than the value shown elsewhere (e.g. the Ask tab's raw
    table cell) for the exact same underlying number.

    Returns '—' for None/NULL values.
    """
    if x is None:
        return "—"
    return f"{_round1(x):.1f}"


def _service_prefix(intent: dict, locale: str) -> str:
    """Optional '{service}の' / '{service} ' prefix when intent narrows by service."""
    service = intent.get("service")
    if not service:
        return ""
    return _t("service_suffix", locale, service=service)


def _no_data(locale: str = "ja") -> str:
    """Default empty-result string for any FORMATTERS branch."""
    return _t("no_data", locale)


def _fmt_ranking(rows: list, intent: dict, locale: str) -> str:
    """Render a ranking report (worst-delay routes) into locale text.

    Row shape from ``compute_ranking`` is
    ``(route_code, service_type, avg_min, p50_min, p90_min, samples)``.
    """
    label = _service_prefix(intent, locale)
    lines = [
        _t(
            "ranking_row",
            locale,
            rank=i,
            route=r[0],
            service=r[1],
            avg=_r(r[2]),
            p50=_r(r[3]),
            p90=_r(r[4]),
            samples=r[5],
        )
        for i, r in enumerate(rows, 1)
    ]
    header = _t("ranking_header", locale, label=label, limit=intent.get("limit", 15))
    return header + "\n" + "\n".join(lines)


def _fmt_on_time(rows: list, intent: dict, locale: str) -> str:
    """Render the on-time-rate ranking. Row: ``(route, service, on_time_pct, avg_min, samples)``."""
    label = _service_prefix(intent, locale)
    lines = [
        _t(
            "on_time_row",
            locale,
            rank=i,
            route=r[0],
            service=r[1],
            pct=_r(r[2]),
            avg=_r(r[3]),
            samples=r[4],
        )
        for i, r in enumerate(rows, 1)
    ]
    return _t("on_time_header", locale, label=label) + "\n" + "\n".join(lines)


def _fmt_worst_5min(rows: list, intent: dict, locale: str) -> str:
    """Render the >5min-delay-count ranking. Row: ``(route, service, late5_count, avg_min, samples)``."""
    label = _service_prefix(intent, locale)
    lines = [
        _t(
            "worst5_row",
            locale,
            rank=i,
            route=r[0],
            service=r[1],
            count=r[2],
            avg=_r(r[3]),
            samples=r[4],
        )
        for i, r in enumerate(rows, 1)
    ]
    return _t("worst5_header", locale, label=label) + "\n" + "\n".join(lines)


def _fmt_dow_ranking(rows: list, intent: dict, locale: str) -> str:
    """Render a DOW-filtered delay ranking.

    Each row is ``(route_code, service_type, dow, avg_min, samples)``. ``dow``
    is an ISODOW int (1..7) or a rollup label string; ``dow_label`` handles
    both shapes and honours the requested locale.
    """
    dow = intent.get("dow", "")
    dow_group = intent.get("dow_group")
    label = _service_prefix(intent, locale)
    lines = [
        _t(
            "dow_row",
            locale,
            rank=i,
            route=r[0],
            service=r[1],
            dow_label=dow_label(r[2], lang=locale),
            avg=_r(r[3]),
            samples=r[4],
        )
        for i, r in enumerate(rows, 1)
    ]
    if dow_group == "weekend":
        header = _t("dow_header_weekend", locale, label=label)
    elif dow_group == "weekday":
        header = _t("dow_header_weekday", locale, label=label)
    else:
        header = _t("dow_header_other", locale, label=label, dow=dow)
    return header + "\n" + "\n".join(lines)


def _fmt_compare_ranking(rows: list, intent: dict, locale: str) -> str:
    """Render the weekday-vs-weekend delta ranking."""
    del intent  # no intent-derived flags today
    lines = []
    for i, r in enumerate(rows, 1):
        signed = float(r[4]) if len(r) >= 5 else (float(r[2] or 0) - float(r[1] or 0))
        if signed > 0:
            direction = _t("dir_weekend_gt", locale)
        elif signed < 0:
            direction = _t("dir_weekday_gt", locale)
        else:
            direction = _t("dir_equal", locale)
        lines.append(
            _t(
                "compare_row",
                locale,
                rank=i,
                route=r[0],
                weekday=_r(r[1]),
                weekend=_r(r[2]),
                delta=_r(r[3]),
                direction=direction,
            )
        )
    return _t("compare_header", locale) + "\n" + "\n".join(lines)


FORMATTERS = {
    "ranking": _fmt_ranking,
    "on_time": _fmt_on_time,
    "worst_5min": _fmt_worst_5min,
    "compare_ranking": _fmt_compare_ranking,
    "dow_ranking": _fmt_dow_ranking,
}


def format_result(query_type: str, rows, intent: dict, locale: str = "ja") -> str:
    """Public entry point: dispatch a result tuple list to its renderer.

    ``rows is None`` signals the caller had no static GTFS data loaded.
    Unknown ``query_type`` and empty ``rows`` both fall back to :func:`_no_data`.
    ``locale`` ∈ {``"ja"``, ``"en"``} switches the rendered language; defaults
    to ``"ja"`` to preserve pre-i18n callers (tests, ad-hoc shells).
    """
    if rows is None:
        return _t("no_static", locale)
    fmt = FORMATTERS.get(query_type)
    if not fmt:
        return _no_data(locale)
    if not rows:
        return _no_data(locale)
    return fmt(rows, intent, locale)


def format_trend_text(days: list, from_date, to_date, locale: str = "ja") -> str:
    """Locale-aware rendering for the Reports trend endpoint's text body.

    ``days`` entries can have ``avg_min=None, samples=0`` for a bucket with
    no observed data (nullable since migration 0028 — see
    ``pipeline.reports.rankings.compute_trend_series``). Those buckets are
    excluded from both the mean and the rendered "observed days" count via
    :func:`pipeline.reports.rankings._weighted_avg_min`'s own NULL-skipping,
    sample-weighted pooling — the same approach the Ask tab's ``time_series``
    tool uses on this same ``compute_trend_series`` output, so the two
    surfaces report the same headline number for identical underlying data.
    An empty ``days`` list hits the same ``avg is None`` branch below as an
    all-NULL one — ``_weighted_avg_min([])`` returns ``None`` too.
    """
    avg = _weighted_avg_min(days)
    if avg is None:
        return _t("trend_empty", locale)
    observed_days = sum(1 for d in days if d.get("avg_min") is not None)
    return _t("trend_header", locale, from_date=from_date, to_date=to_date, avg=avg, days=observed_days)
