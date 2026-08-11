"""Render DigestData to calm Markdown, in ja or en. Pure — no DB, no I/O.

Strings live in _DIGEST_LOCALES with the same (key, lang) shape and JP-fallback
as pipeline/query/tools.py, kept local to the digest package. Calm framing: a
plain headline, bounded movers, quiet pipeline-health footer — no alarm wording.
"""

from typing import Any

from pipeline.digest.models import DigestData

_DIGEST_LOCALES: dict[tuple[str, str], str] = {
    ("title", "ja"): "日次ダイジェスト {day}",
    ("title", "en"): "Daily digest {day}",
    ("network", "ja"): "ネットワーク平均遅延: {avg}分",
    ("network", "en"): "Network avg delay: {avg} min",
    ("network_no_data", "ja"): "対象日のデータがありません。",
    ("network_no_data", "en"): "No data for this day.",
    ("agency_headline", "ja"): "平均 {avg}分（基準比 {delta:+.1f}分）",
    ("agency_headline", "en"): "Avg {avg} min (vs baseline {delta:+.1f} min)",
    ("agency_headline_no_base", "ja"): "平均 {avg}分",
    ("agency_headline_no_base", "en"): "Avg {avg} min",
    ("agency_no_data", "ja"): "{day} のデータなし",
    ("agency_no_data", "en"): "No data for {day}",
    ("mover", "ja"): "- {route}: {avg}分（基準比 {dev:+.1f}分）{conf}",
    ("mover", "en"): "- {route}: {avg} min (vs baseline {dev:+.1f} min){conf}",
    ("movers_none", "ja"): "- 悪化路線なし",
    ("movers_none", "en"): "- No worsening routes",
    ("low_conf", "ja"): " ※少数サンプル",
    ("low_conf", "en"): " (low sample)",
    ("feed_health", "ja"): "{name} フィード健全性: {clamp}/{raw} 件が異常値",
    ("feed_health", "en"): "{name} feed health: {clamp}/{raw} readings clamped",
    ("stale", "ja"): "鮮度警告: 集計が遅延している事業者 {names}",
    ("stale", "en"): "Freshness: aggregates lagging for {names}",
    ("fresh", "ja"): "鮮度: 全事業者最新",
    ("fresh", "en"): "Freshness: all agencies current",
    ("stale_unknown", "ja"): "鮮度: 確認できませんでした",
    ("stale_unknown", "en"): "Freshness: could not be checked",
}


def _t(key: str, lang: str, **vars: Any) -> str:
    if lang not in ("ja", "en"):
        lang = "ja"
    tpl = _DIGEST_LOCALES.get((key, lang)) or _DIGEST_LOCALES.get((key, "ja"), key)
    try:
        return tpl.format(**vars) if vars else tpl
    except (KeyError, IndexError):
        return tpl


def render_digest(data: DigestData, locale: str = "ja") -> str:
    day = data.target_day.isoformat()
    lines = ["## " + _t("title", locale, day=day), ""]

    # Always emit a network summary line: the avg when we have one, else the
    # no-data line. Per-agency sections still render below either way.
    if data.network_avg_delay_min is not None:
        lines.append(_t("network", locale, avg=data.network_avg_delay_min))
    else:
        lines.append(_t("network_no_data", locale))

    if not data.sections:
        return "\n".join(lines)

    for s in data.sections:
        lines += ["", "### " + s.agency_name]
        if not s.has_data:
            lines.append(_t("agency_no_data", locale, day=day))
            continue
        if s.delta_min is not None:
            lines.append(_t("agency_headline", locale, avg=s.avg_delay_min, delta=s.delta_min))
        else:
            lines.append(_t("agency_headline_no_base", locale, avg=s.avg_delay_min))
        if s.movers:
            for m in s.movers:
                conf = _t("low_conf", locale) if m.low_confidence else ""
                lines.append(
                    _t(
                        "mover",
                        locale,
                        route=m.route_code,
                        avg=m.avg_delay_min,
                        dev=m.deviation_min,
                        conf=conf,
                    )
                )
        else:
            lines.append(_t("movers_none", locale))

    lines += ["", "---"]
    for s in data.sections:
        # Skip the feed-health line for an agency with no readings (avoid "0/0").
        if s.raw_samples == 0:
            continue
        lines.append(_t("feed_health", locale, name=s.agency_name, clamp=s.clamp_count, raw=s.raw_samples))
    # staleness_known=False means the ClickHouse probe backing every
    # section's is_stale failed (see build_digest) — every section's
    # is_stale is False in that case too (probe never ran), which is
    # indistinguishable from "probe ran, found nothing stale" unless this
    # flag is checked first. Falling through to "fresh" here would render an
    # affirmative "all agencies current" claim at exactly the moment
    # freshness is actually unknown — worse than saying nothing, since this
    # rendered Markdown is the digest's only output surface.
    if not data.staleness_known:
        lines.append(_t("stale_unknown", locale))
    else:
        stale = [s.agency_name for s in data.sections if s.is_stale]
        lines.append(_t("stale", locale, names=", ".join(stale)) if stale else _t("fresh", locale))
    return "\n".join(lines)
