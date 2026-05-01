import asyncio
import logging
import os
import re

_log = logging.getLogger(__name__)

_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def _reset_groq_client():
    """Reset the client singleton — used in tests via monkeypatch."""
    global _groq_client
    _groq_client = None

_SYSTEM = """\
You are a Japanese bus delay analyst. Reply ONLY in Japanese (日本語).
NEVER use Chinese. NEVER use particles like 的, 是, 有, 了.
Use Japanese particles: は, が, で, に, の, です, ます.
Rules:
- Write route numbers as 系統XXXXX. Never write システム.
- Always cite EXACT departure times and numbers from the data.
- Answer in 1-2 sentences max.
- If the data section contains 【このシステムで回答できる質問】, reply ONLY:
  「その質問には対応していません。」 followed by one closest example."""

_PROMPT = """\
【データ】
{context}

【質問】
{question}

データに数値がある場合はそのまま使って答えてください。数値を省略したり言い換えたりしないこと。
データに答えがない場合は「その質問には対応していません。」と答えてください。"""

_FIX_RE = re.compile(r"\b[Ss]ystem\b|(?<![ァ-ヴ])システム(?![ァ-ヴ])")


def _fix(text: str) -> str:
    return _FIX_RE.sub("系統", text).replace("系统", "系統")


def _no_data(label: str = "") -> str:
    return f"{label}データがありません。" if label else "データがありません。"


def _route_scope_label(intent: dict) -> str:
    route_name = intent.get("route_name")
    if route_name:
        return f"路線{route_name}"
    route = intent.get("route")
    if route:
        return f"系統{route}"
    return "対象"


def _fmt_ranking(rows: list, intent: dict) -> str:
    if not rows:
        return ""
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）平均{r[2]}分、p50={r[3]}分、p90={r[4]}分（{r[5]}件）"
        for i, r in enumerate(rows, 1)
    ]
    return f"【{label}遅延ランキング上位{intent.get('limit', 15)}系統】\n" + "\n".join(lines)


def _fmt_by_hour(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    lines = [
        f"系統{r[0]}（{r[1]}）{r[2]}発: 平均{r[3]}分、p50={r[4]}分、p90={r[5]}分（{r[6]}件）"
        for r in rows
    ]
    return f"【{scope} 発車時刻別遅延】\n" + "\n".join(lines)


def _fmt_by_dow(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    dow = intent.get("dow")
    dow_group = intent.get("dow_group")
    lines = [f"系統{r[0]}（{r[1]}）{r[2]}: 平均{r[3]}分（{r[4]}件）" for r in rows]
    if dow:
        header = f"【{scope} {dow}曜の遅延】"
    elif dow_group == "weekend":
        header = f"【{scope} 週末遅延】"
    elif dow_group == "weekday":
        header = f"【{scope} 平日遅延】"
    else:
        header = f"【{scope} 曜日別遅延】"
    return f"{header}\n" + "\n".join(lines)


def _fmt_by_stop(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    stop_name = intent.get("stop_name")
    label = f"「{stop_name}」付近 " if stop_name else ""
    lines = [f"{r[1] or str(r[0]) + '番停留所'}（{r[0]}番）: 平均{r[2]}分（{r[3]}件）" for r in rows]
    return f"【{scope} {label}停留所別遅延（上位{len(rows)}）】\n" + "\n".join(lines)


def _fmt_by_date(rows: list, intent: dict) -> str:
    date_value = intent.get("date", "")
    lines = [f"系統{r[0]}（{r[1]}）: 平均{r[2]}分（{r[3]}件）" for r in rows]
    return f"【{date_value} 遅延データ】\n" + "\n".join(lines)


def _fmt_trend(rows: list, intent: dict) -> str:
    if not rows:
        return "トレンド計算に必要なデータが不足しています（28日以上のデータが必要）。"
    lines = [
        f"{'↑悪化' if r[4] > 0 else '↓改善'} 系統{r[0]}（{r[1]}）: "
        f"直近{r[2]}分、前期{r[3]}分（{'+' if r[4] > 0 else ''}{r[4]}分）"
        for r in rows
    ]
    return "【遅延トレンド（直近14日 vs 前14日）】\n" + "\n".join(lines)


def _fmt_on_time(rows: list, intent: dict) -> str:
    route = intent.get("route")
    route_name = intent.get("route_name")
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    if route or route_name:
        scope = _route_scope_label(intent)
        lines = [
            f"系統{r[0]}（{r[1]}）: 定時率{r[3]}%、5分超遅延率{r[4]}%、平均遅延{r[2]}分（{r[5]}件）"
            for r in rows
        ]
        return f"【{scope} 定時率】\n" + "\n".join(lines)
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）定時率{r[2]}%、平均{r[3]}分（{r[4]}件）"
        for i, r in enumerate(rows, 1)
    ]
    return f"【{label}定時率ランキング】\n" + "\n".join(lines)


def _fmt_compare(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    lines = [f"{scope}（{r[0]}）: 平均{r[1]}分（{r[2]}件）" for r in rows]
    by_service = {r[0]: r[1] for r in rows}
    heijitsu = by_service.get("平日")
    kyujitsu = by_service.get("土日祝")
    verdict = ""
    if heijitsu is not None and kyujitsu is not None:
        delta = round(abs(kyujitsu - heijitsu), 2)
        if kyujitsu > heijitsu:
            verdict = f"\n判定: 土日祝のほうが{delta}分遅いです。"
        elif heijitsu > kyujitsu:
            verdict = f"\n判定: 平日のほうが{delta}分遅いです。"
        else:
            verdict = "\n判定: 平日と土日祝の平均遅延は同程度です。"
    return f"【{scope} 平日・土日祝比較】\n" + "\n".join(lines) + verdict


def _fmt_worst_5min(rows: list, intent: dict) -> str:
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [
        f"{i}位: 系統{r[0]}（{r[1]}）5分超: {r[3]}回、平均{r[2]}分（{r[4]}件）"
        for i, r in enumerate(rows, 1)
    ]
    return f"【{label}5分超遅延ランキング】\n" + "\n".join(lines)


def _fmt_stop_ranking(rows: list, intent: dict) -> str:
    limit = intent.get("limit", 15)
    stop_name = intent.get("stop_name")
    label = f"「{stop_name}」付近 " if stop_name else ""
    lines = [
        f"{i}位: 系統{r[0]} {r[2] or str(r[1]) + '番停留所'}（{r[1]}番）: 平均{r[3]}分（{r[4]}件）"
        for i, r in enumerate(rows, 1)
    ]
    return f"【{label}停留所別遅延ランキング上位{limit}】\n" + "\n".join(lines)


def _fmt_dow_ranking(rows: list, intent: dict) -> str:
    dow = intent.get("dow", "")
    dow_group = intent.get("dow_group")
    label = f"{intent.get('service')}の" if intent.get("service") else ""
    lines = [f"{i}位: 系統{r[0]}（{r[1]}）{r[2]}: 平均{r[3]}分（{r[4]}件）" for i, r in enumerate(rows, 1)]
    if dow_group == "weekend":
        header = f"【{label}週末遅延ランキング】"
    elif dow_group == "weekday":
        header = f"【{label}平日遅延ランキング】"
    else:
        header = f"【{label}{dow}曜日遅延ランキング】"
    return f"{header}\n" + "\n".join(lines)


def _fmt_compare_ranking(rows: list, intent: dict) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        signed = r[4] if len(r) >= 5 else (r[2] - r[1])
        direction = "土日祝>平日" if signed > 0 else ("平日>土日祝" if signed < 0 else "同程度")
        lines.append(
            f"{i}位: 系統{r[0]} 平日{r[1]}分 / 土日祝{r[2]}分（差: {r[3]}分, {direction}）"
        )
    return "【平日・土日祝 遅延差ランキング】\n" + "\n".join(lines)


def _fmt_stop_list(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    lines = [
        f"{r[0]}番: {r[1]}（{r[2]}発）" if r[2] else f"{r[0]}番: {r[1]}"
        for r in rows
    ]
    return f"【{scope} 停車駅一覧（{len(rows)}駅）】\n" + "\n".join(lines)


def _fmt_routes_at_stop(rows: list, intent: dict) -> str:
    stop_name = intent.get("stop_name", "")
    actual_stop = rows[0][2] if rows else stop_name
    def _code(route_id: str) -> str:
        m = re.search(r"\((\d+)\)", route_id)
        return m.group(1) if m else route_id
    lines = [f"系統{_code(r[0])} {r[1]}" for r in rows]
    return f"【「{actual_stop}」を経由する系統（{len(rows)}系統）】\n" + "\n".join(lines)


def _fmt_route_info(rows: list, intent: dict) -> str:
    if not rows:
        return ""
    r = rows[0]
    route_id, route_short_name, stop_count, first_dep, last_dep, trip_count = r
    m = re.search(r"\((\d+)\)", route_id)
    code = m.group(1) if m else route_id
    return (
        f"【系統{code} 路線情報】\n"
        f"路線名: {route_short_name}\n"
        f"停留所数: {stop_count}駅\n"
        f"始発: {first_dep or '不明'}　最終: {last_dep or '不明'}\n"
        f"運行便数: {trip_count}便"
    )


def _fmt_timetable(rows: list, intent: dict) -> str:
    scope = _route_scope_label(intent)
    stop_name = intent.get("stop_name")
    service = intent.get("service")
    svc_label = f"（{service}）" if service else ""
    if stop_name and rows and len(rows[0]) >= 3:
        actual_stop = rows[0][2]
        header = f"【{scope}{svc_label}「{actual_stop}」発時刻表（{len(rows)}便）】"
        lines = [r[0] for r in rows]
    else:
        header = f"【{scope}{svc_label} 時刻表（始発停留所発、{len(rows)}便）】"
        lines = [f"{r[0]} {r[1]}行き" if r[1] else r[0] for r in rows]
    return f"{header}\n" + "\n".join(lines)


_NO_STATIC_MSG = (
    "この質問にはGTFS Staticデータが必要です。\n"
    "`python gtfs_rag_pipeline.py load_static ./raw_archives_static` を先に実行してください。"
)

FORMATTERS = {
    "ranking":          _fmt_ranking,
    "by_hour":          _fmt_by_hour,
    "by_dow":           _fmt_by_dow,
    "by_stop":          _fmt_by_stop,
    "by_date":          _fmt_by_date,
    "trend":            _fmt_trend,
    "on_time":          _fmt_on_time,
    "compare":          _fmt_compare,
    "worst_5min":       _fmt_worst_5min,
    "stop_ranking":     _fmt_stop_ranking,
    "dow_ranking":      _fmt_dow_ranking,
    "compare_ranking":  _fmt_compare_ranking,
    "stop_list":        _fmt_stop_list,
    "routes_at_stop":   _fmt_routes_at_stop,
    "route_info":       _fmt_route_info,
    "timetable":        _fmt_timetable,
}


def format_result(query_type: str, rows, intent: dict) -> str:
    if rows is None:
        return _NO_STATIC_MSG
    fmt = FORMATTERS.get(query_type)
    if not fmt:
        return _no_data()
    if not rows:
        # Let formatters that handle empty rows produce their own message;
        # fall back to generic _no_data() for all others.
        result = fmt(rows, intent)
        return result if result else _no_data()
    return fmt(rows, intent)


async def format_guidance_menu(conn, agency_id: int) -> str:
    try:
        rows = await conn.fetch(
            "SELECT route_code, service_type, avg_min, p50_min, p90_min, samples "
            "FROM agg_route_stats WHERE agency_id=$1 ORDER BY avg_min DESC LIMIT 10",
            agency_id,
        )
        rows = [tuple(r) for r in rows]
    except Exception as exc:
        _log.warning("format_guidance_menu DB error: %s", exc)
        rows = []

    ranking = (
        "\n".join(
            f"{i}位: 系統{r[0]}（{r[1]}）平均{r[2]}分、p50={r[3]}分、p90={r[4]}分（{r[5]}件）"
            for i, r in enumerate(rows, 1)
        )
        if rows else "（データなし）"
    )

    return (
        f"【遅延ランキング上位10系統】\n{ranking}\n\n"
        "【このシステムで回答できる質問】\n"
        "- 遅延ランキング（例：一番遅延が多い系統は？）\n"
        "- 5分超遅延が多い系統（例：遅延が大きい系統は？）\n"
        "- 発車時刻別遅延（例：系統44372は何時台が遅い？）\n"
        "- 曜日別遅延（例：系統44372は何曜日が遅い？）\n"
        "- 停留所別遅延（例：系統44372はどの停留所で遅延が多い？）\n"
        "- 平日と土日祝の比較（例：系統22171の平日と土日祝どちらが遅い？）\n"
        "- 平日・土日祝遅延差ランキング（例：休日に特に遅延する系統は？）\n"
        "- 定時率（例：系統44372の定時率は？）\n"
        "- 特定日付（例：4月10日の遅延は？）\n"
        "- 遅延トレンド（例：最近遅延が増えている系統は？）\n"
        "【GTFSスタティック（load_static後に利用可能）】\n"
        "- 停車駅一覧（例：系統16071の停車駅をその順番と一緒に教えて）\n"
        "- バス停を経由する系統（例：青森駅に停まる系統は？）\n"
        "- 路線情報（例：系統44372の路線名と始発・最終は？）\n"
        "- 時刻表（例：系統44372の時刻表を教えて）\n"
    )


async def format_unknown(
    question: str, conn=None, agency_id: int = 0, model: str = "llama-3.2-11b-text-preview"
) -> str:
    context = await format_guidance_menu(conn, agency_id) if conn is not None else ""
    prompt = _PROMPT.format(context=context, question=question) if context else question

    client = _get_groq_client()

    def _sync():
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                stream=True,
            )
            return "".join(chunk.choices[0].delta.content or "" for chunk in stream)
        except Exception:
            return "申し訳ありません、エラーが発生しました。"

    raw = await asyncio.to_thread(_sync)
    return _fix(raw)
