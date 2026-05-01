"""Intent classifier: maps Japanese bus delay questions to structured query intents."""

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta

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

VALID_QUERY_TYPES = {
    "ranking", "by_hour", "by_dow", "by_stop", "by_date",
    "trend", "on_time", "compare", "worst_5min", "stop_ranking", "dow_ranking",
    "compare_ranking",
    "stop_list", "routes_at_stop", "route_info", "timetable",
}

_VALID_DOW = {"月", "火", "水", "木", "金", "土", "日"}
_VALID_TIME_BANDS = {"morning", "day", "evening", "night", "rush"}
_VALID_DOW_GROUPS = {"weekend", "weekday"}
_VALID_TREND_DIRECTION = {"any", "up", "down"}
_VALID_COMPARE_POLARITY = {"any", "holiday_worse", "weekday_worse"}
_VALID_SORT_ORDER = {"desc", "asc"}
_ROUTE_REQUIRED = {"by_hour", "by_dow", "by_stop", "compare", "stop_list", "route_info", "timetable"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_RELATIVE_DATE_OFFSETS = {
    "今日": 0, "きょう": 0, "本日": 0,
    "昨日": -1, "きのう": -1,
    "一昨日": -2, "おととい": -2,
}

_TIME_BAND_MAP = {
    "朝": "morning", "午前": "morning", "昼": "day", "午後": "day",
    "夕方": "evening", "夜": "night", "深夜": "night", "ラッシュ": "rush",
    "morning": "morning", "day": "day", "evening": "evening", "night": "night", "rush": "rush",
}

_DOW_GROUP_MAP = {
    "週末": "weekend", "土日": "weekend", "土日祝": "weekend", "weekend": "weekend",
    "平日": "weekday", "weekday": "weekday",
}

_TREND_DIRECTION_MAP = {
    "up": "up", "増加": "up", "増えている": "up", "悪化": "up",
    "down": "down", "改善": "down", "減少": "down", "any": "any",
}

_COMPARE_POLARITY_MAP = {
    "holiday_worse": "holiday_worse", "休日が遅い": "holiday_worse", "土日祝が遅い": "holiday_worse",
    "weekday_worse": "weekday_worse", "平日が遅い": "weekday_worse", "any": "any",
}

_SORT_ORDER_MAP = {"desc": "desc", "降順": "desc", "昇順": "asc", "asc": "asc"}

INTENT_SYSTEM_PROMPT = """\
You are an intent classifier for a Japanese bus delay analysis system.
Given a user question in Japanese, extract the query intent as JSON.
Return ONLY a valid JSON object — no explanation, no markdown.

== QUERY TYPES ==

"ranking" — sorted by AVERAGE delay time, all routes
"worst_5min" — sorted by COUNT of trips delayed >5 minutes
"by_hour" — departure-time breakdown for ONE specific route (route or route_name required)
"by_dow" — day-of-week breakdown for ONE specific route (route or route_name required)
"by_stop" — stop breakdown for ONE specific route (route or route_name required)
"by_date" — delay on a specific calendar date
"trend" — compares recent 14 days vs prior 14 days
"on_time" — on-time rate (定時率)
"compare" — weekday vs holiday comparison for ONE specific route (route or route_name required)
"compare_ranking" — which routes have the BIGGEST difference between weekday and holiday (no route required)
"stop_ranking" — worst stops across ALL routes
"dow_ranking" — worst routes on a specific day or day-group
"stop_list" — ordered stop sequence for a route (route or route_name required)
"routes_at_stop" — which routes stop at a named bus stop (stop_name required, no route)
"route_info" — route basic metadata (route or route_name required)
"timetable" — departure times for a route (route or route_name required)

== FIELDS ==
- query_type: one of the 16 values above
- route: digits only (e.g. "44372"), or null
- route_name: route short name text when user uses 路線名, or null
- service: "平日", "土日祝", or null
- dow: single kanji "月" "火" "水" "木" "金" "土" "日", or null
- dow_group: "weekend" | "weekday" | null
- date: "YYYY-MM-DD" or null
- stop_name: partial bus-stop name, or null
- time_band: "morning" | "day" | "evening" | "night" | "rush" | null
- trend_direction: "up" | "down" | "any"
- compare_polarity: "holiday_worse" | "weekday_worse" | "any"
- sort_order: "desc" | "asc"
- limit: integer 3-100, default 15
- unknown: true only if question matches NONE of the 16 types
"""


def _normalize_date_token(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s in _RELATIVE_DATE_OFFSETS:
        return (date.today() + timedelta(days=_RELATIVE_DATE_OFFSETS[s])).isoformat()
    if _DATE_RE.match(s):
        return s
    mdy = re.match(r"^(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日$", s)
    if mdy:
        y = int(mdy.group(1)) if mdy.group(1) else date.today().year
        m = int(mdy.group(2))
        d = int(mdy.group(3))
        try:
            return datetime(y, m, d).date().isoformat()
        except ValueError:
            return None
    return None


def validate_intent(raw: dict) -> dict:
    """Validate and normalise a raw intent dict from the LLM."""
    intent = {
        "query_type": raw.get("query_type", "unknown"),
        "route": None, "route_name": None, "service": None,
        "dow": None, "dow_group": None, "date": None,
        "stop_name": None, "time_band": None,
        "trend_direction": "any", "compare_polarity": "any",
        "sort_order": "desc", "limit": 15,
        "unknown": bool(raw.get("unknown", False)),
    }

    if intent["query_type"] not in VALID_QUERY_TYPES:
        intent["query_type"] = "unknown"
        intent["unknown"] = True
        return intent

    route_name_raw = raw.get("route_name")
    route_name = str(route_name_raw).strip() if route_name_raw else None

    route = raw.get("route")
    if route is not None:
        route = str(route).strip()
        if route.isdigit():
            intent["route"] = route
        elif not route_name:
            if re.search(r"[ぁ-んァ-ヴ一-龥]|路線|系統|\(|\s", route):
                route_name = route

    if route_name:
        route_name = re.sub(r"^路線", "", route_name).strip()[:80]
        if not intent["route"]:
            if route_name.isdigit():
                intent["route"] = route_name
            else:
                m = re.search(r"\((\d+)\)", route_name)
                if m:
                    intent["route"] = m.group(1)
        intent["route_name"] = route_name or None

    service = raw.get("service")
    if service in ("土日", "祝日", "休日"):
        service = "土日祝"
    if service not in ("平日", "土日祝"):
        service = None
    intent["service"] = service

    dow = raw.get("dow")
    if dow in ("週末", "土日"):
        intent["dow_group"] = "weekend"
        dow = None
    elif dow in ("平日", "月〜金"):
        intent["dow_group"] = "weekday"
        dow = None
    intent["dow"] = dow if dow in _VALID_DOW else None

    dow_group = raw.get("dow_group")
    if dow_group is not None:
        dg = _DOW_GROUP_MAP.get(str(dow_group).strip(), str(dow_group).strip())
        if dg in _VALID_DOW_GROUPS:
            intent["dow_group"] = dg

    intent["date"] = _normalize_date_token(raw.get("date"))

    sn = raw.get("stop_name")
    if sn:
        sn = str(sn).strip()[:50]
    intent["stop_name"] = sn or None

    tb = raw.get("time_band")
    if tb is not None:
        tb = _TIME_BAND_MAP.get(str(tb).strip(), str(tb).strip())
    intent["time_band"] = tb if tb in _VALID_TIME_BANDS else None

    td = raw.get("trend_direction")
    if td is not None:
        td = _TREND_DIRECTION_MAP.get(str(td).strip(), str(td).strip())
    intent["trend_direction"] = td if td in _VALID_TREND_DIRECTION else "any"

    cp = raw.get("compare_polarity")
    if cp is not None:
        cp = _COMPARE_POLARITY_MAP.get(str(cp).strip(), str(cp).strip())
    intent["compare_polarity"] = cp if cp in _VALID_COMPARE_POLARITY else "any"

    so = raw.get("sort_order")
    if so is not None:
        so = _SORT_ORDER_MAP.get(str(so).strip(), str(so).strip())
    intent["sort_order"] = so if so in _VALID_SORT_ORDER else "desc"

    try:
        intent["limit"] = max(3, min(100, int(raw.get("limit", 15))))
    except (TypeError, ValueError):
        intent["limit"] = 15

    if intent["query_type"] in _ROUTE_REQUIRED and not (intent["route"] or intent["route_name"]):
        intent["unknown"] = True
    if intent["query_type"] == "dow_ranking" and not (intent["dow"] or intent["dow_group"]):
        intent["unknown"] = True
    if intent["query_type"] == "by_date" and not intent["date"]:
        intent["unknown"] = True
    if intent["query_type"] == "routes_at_stop" and not intent["stop_name"]:
        intent["unknown"] = True

    if intent["dow"]:
        intent["dow_group"] = None

    return intent


async def classify_intent(question: str, model: str = "llama-3.2-11b-text-preview") -> dict:
    """Send question to Groq LLM and return validated intent dict."""
    client = _get_groq_client()

    def _sync():
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(response.choices[0].message.content or "")

    raw = await asyncio.to_thread(_sync)
    return validate_intent(raw)
