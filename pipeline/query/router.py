"""Pre-LLM router: regex rules that dispatch a question straight to a tool.

:func:`match_rules` is the whole router. A question no rule matches returns
``None`` and the caller falls through to the LLM stage in
:mod:`pipeline.query.chat`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    # A question that also matches `unless` asks for something this rule's
    # args cannot express, so the rule does not fire for it.
    unless: re.Pattern | None = None


@dataclass
class RouterDecision:
    tool: str
    args: dict[str, Any]
    score: float
    matched_pattern: str | None


# Words that turn a ranking question to the other end of its list. Each one
# feeds both the rule that asks for that end and the `unless` of the rule that
# cannot, so the two stay the same list. A rank word asks for a ranking by
# itself; a trait word ("定時率が低い理由") does only beside a ranking marker.
_LOW_ON_TIME_RANKS = r"ワースト|下位"
_LOW_ON_TIME_TRAITS = r"低い|低め|悪い|最低"
_LOW_ON_TIME_WORDS = rf"{_LOW_ON_TIME_RANKS}|{_LOW_ON_TIME_TRAITS}"
_LITTLE_DELAY_WORDS = r"少な|短い|小さい|低い"
# "N分以上/超/を超える" names a delay threshold. Only 5 minutes is a metric the
# router knows (worst_5min), which is matched by its own rule before the
# average-delay rules; a ranking over any other threshold has no rule.
_DELAY_THRESHOLD = r"\d+\s*分\s*(?:を\s*)?(?:超|越|以上)"

# The meta rules answer questions about what the dataset holds (routes, date
# range, stops). A delay word or a route code (4+ digits) marks an analytic
# question about the data itself, which the meta answers would replace with an
# answer to a different question, so such a question is left to the LLM stage.
_DELAY_WORDS = r"遅延|遅れ|遅く"
_ANALYTIC_WORDS = rf"{_DELAY_WORDS}|\d{{4,}}"
# The ranking rules rank routes; a question about stops has no rule here.
_STOP_WORDS = r"停留所|バス停"

# Compile regexes ONCE at import time. First match wins (priority = order).
_RULES: list[Rule] = [
    # ---- describe_data meta-tool fan-out ----
    Rule(
        name="meta-routes",
        pattern=re.compile(r"(どんな.*?(路線|系統))|((路線|系統).*?(一覧|リスト))|(何.*?路線.*?(ある|登録))"),
        tool="describe_data",
        args={"kind": "routes"},
        # An on-time list is ranking-on-time's to answer, a sample-count list
        # meta-sample-counts'.
        unless=re.compile(rf"{_ANALYTIC_WORDS}|定時率|サンプル数"),
    ),
    Rule(
        name="meta-date-range",
        # "いつ" must be followed directly (or by 頃/ごろ) by から/まで:
        # "いつも…から" is a habit, not a date.
        pattern=re.compile(r"(いつ(頃|ごろ)?(から|まで))|((最新|最古).*?(データ|観測))|(何件.*?(観測|データ))"),
        tool="describe_data",
        args={"kind": "date_range"},
        unless=re.compile(_ANALYTIC_WORDS),
    ),
    Rule(
        name="meta-stops",
        pattern=re.compile(r"停留所.*?(いくつ|何個|一覧)"),
        tool="describe_data",
        args={"kind": "stops"},
        unless=re.compile(_ANALYTIC_WORDS),
    ),
    Rule(
        name="meta-agencies",
        pattern=re.compile(r"(何社|何個).*?事業者|事業者.*?(一覧|何社)"),
        tool="describe_data",
        args={"kind": "agencies"},
    ),
    Rule(
        name="meta-overview",
        pattern=re.compile(r"(全体|データセット).*?(概要|概況)|概要を"),
        tool="describe_data",
        args={"kind": "overview"},
        # A delay or on-time word marks a metric question and a named route a
        # single-route one; neither asks about the dataset. A bare digit run
        # may be a date, so it does not count.
        unless=re.compile(rf"{_DELAY_WORDS}|定時率|(?:路線|系統)\s*\d+"),
    ),
    Rule(
        name="meta-metrics",
        # Require enumeration intent so definition questions
        # ("〜という指標の意味") don't over-fire into describe_data.
        pattern=re.compile(r"(指標|メトリクス).*?(一覧|何|計算でき)|計算できる.*?(指標|メトリクス)"),
        tool="describe_data",
        args={"kind": "metrics"},
    ),
    Rule(
        name="meta-sample-counts",
        pattern=re.compile(r"サンプル数.*?(多い|TOP|ランキング)"),
        tool="describe_data",
        args={"kind": "sample_counts"},
    ),
    # ---- top_n ----
    # More-specific rules precede `ranking-worst` (first match wins), so e.g.
    # "5分以上の遅れが多い系統TOP10" reaches `ranking-worst-5min`. A ranking's
    # default order is one end of its list; a low/few phrasing asks for the
    # other, which the `-low`/`-least` rules supply. The default-order rules
    # skip such a question, so one no rule can place falls through instead.
    Rule(
        name="ranking-worst-5min",
        # The count is tried first, so "…が多い路線TOP3" reads its 3 rather than
        # stopping at 多い; "5分" must not be the tail of "15分" or "2.5分".
        pattern=re.compile(
            r"(?<![\d.．])5分.*?(?:超|以上)(?:.*?(?:TOP|ワースト|上位)\s*(\d+)|.*?(?:多い|TOP|ワースト|上位|ランキング))"
        ),
        tool="top_n",
        args={"metric": "worst_5min", "n": 10},
        # worst_5min has no fewest-first order.
        unless=re.compile(rf"{_LITTLE_DELAY_WORDS}|{_STOP_WORDS}"),
    ),
    Rule(
        name="ranking-on-time-low",
        # The count follows the rank word: "ワースト5", "低い路線TOP5".
        pattern=re.compile(
            rf"定時率.*?(?:(?:{_LOW_ON_TIME_RANKS})\s*(\d+)?"
            rf"|(?:{_LOW_ON_TIME_TRAITS}).*?(?:TOP|ランキング|一覧|順|{_LOW_ON_TIME_RANKS})\s*(\d+)?)"
        ),
        tool="top_n",
        args={"metric": "on_time_rate", "n": 10, "best_first": False},
        unless=re.compile(_STOP_WORDS),
    ),
    Rule(
        name="ranking-on-time",
        pattern=re.compile(r"定時率(?:.*?TOP\s*(\d+)|.*?(?:TOP|ランキング|高い))"),
        tool="top_n",
        args={"metric": "on_time_rate", "n": 10},
        unless=re.compile(rf"{_LOW_ON_TIME_WORDS}|{_STOP_WORDS}"),
    ),
    Rule(
        name="ranking-least-delay",
        pattern=re.compile(rf"(遅延|遅れ).*?({_LITTLE_DELAY_WORDS}).*?(ワースト|TOP|ランキング)\s*(\d+)?"),
        tool="top_n",
        args={"metric": "avg_delay", "n": 10, "best_first": True},
        # Fewest >5min incidents is a different metric from least mean delay.
        unless=re.compile(rf"5分|{_DELAY_THRESHOLD}|{_STOP_WORDS}"),
    ),
    Rule(
        name="ranking-worst",
        pattern=re.compile(r"(遅延|遅れ).*?(ワースト|TOP)\s*(\d+)?"),
        tool="top_n",
        args={"metric": "avg_delay", "n": 10},
        # A delay threshold asks for a count of long delays, not the mean
        # ranking this rule answers (5 minutes is taken by ranking-worst-5min).
        unless=re.compile(rf"{_LITTLE_DELAY_WORDS}|{_DELAY_THRESHOLD}|{_STOP_WORDS}"),
    ),
    # ---- capabilities fallback for app-help-y phrasings ----
    Rule(
        name="capabilities-help",
        # Anchor at start or after a delimiter so "何ができ" doesn't fire
        # mid-sentence inside an unrelated question.
        pattern=re.compile(r"(^|[、。\s])(何ができ|使い方|どう使う|使える機能)"),
        tool="capabilities",
        args={},
    ),
    # ---- deterministic out-of-scope guard (lowest priority) ----
    # Clearly out-of-scope topics route to capabilities so refusals stay
    # deterministic even when the LLM is unavailable.
    Rule(
        name="oos-guard",
        pattern=re.compile(r"(天気|気温|降水|運賃|料金|定期券|事故|遅延証明|駐車場|忘れ物|時刻表のPDF)"),
        tool="capabilities",
        args={},
    ),
]


def _validate_rules() -> None:
    from pipeline.query.tools import _HANDLERS

    known = set(_HANDLERS.keys())
    bad = [r.name for r in _RULES if r.tool not in known]
    if bad:
        raise RuntimeError(f"Router has rules pointing at unknown tools: {bad}. Known tools: {sorted(known)}")


_validate_rules()


# Phrasings that point back at the previous turn's result ("that", "the same",
# "earlier"). A first turn has no result for them to point at, so a rule
# matching the rest of the question would answer something else.
_BACK_REFERENCE_WORDS = r"他には|さっき|前の(?:やつ|結果|に戻)|同じ(?:条件|系統|の|こと)|それを|そのまま"

# Phrasings that read as continuing the previous turn's list (pagination,
# "show me more") or refining it (re-sort, filter, "another one");
# needs_previous_turn() decides which of them cannot be answered without that
# turn. Four alternatives are narrowed because a self-contained question can
# contain a look-alike word:
#   * もっと must not swallow もっとも ("most" — a superlative, not "more").
#   * 次の/前の must not catch literal "next/previous stop" data questions.
#     They only count as follow-ups before continuation/pagination words.
#   * 別の must not catch 別の府 ("another prefecture", as in 別の府県のデータ),
#     nor 別 as the suffix of a kanji or katakana word: 月別の/路線別の/ルート別の
#     are "by X" breakdowns and 特別の is "special". After hiragana, after
#     全然 ("completely"), or at the start (それとは別の, 全然別の, 別の日) it
#     still reads as "another".
#   * English words count only whole and at the end of the question, so
#     "next"/"more" do but "same-day comparison" or "next stop information"
#     do not (favors false negatives, which are safe).
_ENGLISH_FOLLOW_UP_END = r"\b\s*[.!?]?\s*$"
# CJK ideographs (with extension A and compatibility forms), 々, and full- and
# half-width katakana including ー: the characters a word that takes 別 as a
# suffix ends in.
_KANJI_OR_KATAKANA = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々\u30a0-\u30ff\uff66-\uff9f"
_CONTINUATION_WORDS = (
    r"もっと(?!も)"  # もっと but NOT もっとも (superlative "most")
    r"|もう少し|続き|つづき|残り"
    r"|次の(?:\d+件|\d*ページ|やつ|分(?!.*停留所)|ぶん)|次のページ|\d+ページ目"  # 次の<page-ish>, not 次の停留所
    r"|前の\d+件"  # 前の<page>, not 前の停留所
    rf"|\b(?:more|next|again|continue|previous|same){_ENGLISH_FOLLOW_UP_END}"
)
_REFINEMENT_WORDS = (
    r"逆順|逆に|昇順|降順|並べ替え|並び替え|絞り込|さらに"
    rf"|(?:(?<=全然)|(?<![{_KANJI_OR_KATAKANA}]))別の(?!府)"
    rf"|\breverse{_ENGLISH_FOLLOW_UP_END}"
)
_FOLLOW_UP_RE = re.compile(rf"{_CONTINUATION_WORDS}|{_BACK_REFERENCE_WORDS}|{_REFINEMENT_WORDS}")
_BACK_REFERENCE_RE = re.compile(_BACK_REFERENCE_WORDS)
_CONTINUATION_RE = re.compile(_CONTINUATION_WORDS)
_REFINEMENT_RE = re.compile(_REFINEMENT_WORDS)

# What a refinement can apply to without a previous turn: a transit entity or
# metric the question names (matched on folded text). A filter value alone
# (平日, 2025年) or a rank marker (ワースト10) names nothing to rank, so it
# does not count. 便 skips 便利/不便/郵便.
_SUBJECT_RE = re.compile(
    rf"路線|系統|{_STOP_WORDS}|{_DELAY_WORDS}|定時率|事業者|(?<![不郵])便(?!利)"
    r"|(?<![a-z])(?:routes?|stops?|delay(?:s|ed)?|on[- ]?time)(?![a-z])"
)


def is_follow_up(question: str) -> bool:
    """True when a question's wording reads as a continuation of the previous turn.

    With history present the API sends such a question straight to the LLM
    stage with that history attached, since a stateless rule
    match cannot see what it continues.
    """
    return bool(_FOLLOW_UP_RE.search((question or "").strip()))


def needs_previous_turn(question: str, *, router_runs: bool = True) -> bool:
    """True when a follow-up question cannot be answered on its own.

    A back-reference always needs a previous turn. Continuation wording
    (pagination, "more") needs one unless a rule answers the
    question. Refinement wording (re-sort, filter, "another") needs one only
    when the question names no subject of its own to refine, and then
    likewise unless a rule answers it; a noun that belongs to a reference
    back ("その路線") is not such a subject. With *router_runs* false no rule
    will see the question, so none answers it.
    """
    text = (question or "").strip()
    if _BACK_REFERENCE_RE.search(text):
        return True
    needs_prior_result = bool(_CONTINUATION_RE.search(text)) or (
        bool(_REFINEMENT_RE.search(text)) and not _has_own_subject(text)
    )
    return needs_prior_result and not (router_runs and match_rules(text) is not None)


# Words that point at something the conversation already named ("その路線",
# "それの内訳", "the same route"). The lookarounds drop words that only contain
# a demonstrative (その他, それぞれ, それでも, この1週間, このシステム); the
# needs_history tests list them. English "that"/"this" count only where they
# cannot be a relative pronoun or a "this week" window: before a transit noun,
# or as a pronoun after a preposition or at the end of the question.
_ANAPHORA_RE = re.compile(
    r"その(?!他|ほか|もの)"
    r"|(?<!お)それ(?!ぞれ|とも|ほど|でも|にしても)"
    r"|これ(?!まで|から|ほど)"
    r"|この(?![\d一二三四五六七八九十数半週月年春夏秋冬]|先|頃|ごろ|ところ|まま|間|あいだ"
    r"|システム|アプリ|サービス|サイト|ページ|画面|ツール)"
    r"|あの(?![ーうさね、,\s])"
    r"|あれ(?![ばるた]|ほど)"
    r"|(?<!そこ)そこ(?!そこ)|そちら|こちら(?!で)|あちら"
    r"|同じ(?:路線|系統|停留所|バス停|駅|日|時間|期間|曜日|区間|便|事業者|条件|の|こと|もの|やつ)"
    r"|当該|上記|前述"
    r"|\b(?:it|them|those)\b|\bthese\b(?!\s+days\b)"
    r"|\bsame\b(?!-)"
    r"|\b(?:that|this)\s+(?:routes?|lines?|stops?|stations?|bus(?:es)?|trips?|days?|dates?|periods?|hours?"
    r"|agency|agencies|operators?|ones?)\b"
    r"|\b(?:of|about|for|on|in|with|like|than|from)\s+(?:that|this)\b(?!\s+[a-z])"
    r"|\b(?:that|this)\s*[.!?]?\s*$"
)

# A route the question names itself: a 4-5 digit code ("22171"), a numbered
# 系統/路線 ("系統5") or a one-letter alias ("A1", "L21"). A count or rank
# ("ワースト5", "TOP10", "p90", "1000件"), a year, date or duration ("2025年",
# "2026-10-20", "1200秒") and a stop number ("停留所1234") are not one, since
# they leave a referenced route unresolved. A 3-digit code or "1番" is left
# unmatched because it reads the same as a count; missing one only sends the
# question to the LLM with its history.
_OWN_ROUTE_RE = re.compile(
    r"(?<![\d/.-])(?<!停留所)(?<!バス停)\d{4,5}(?![\d/.:-]|\s*[年月日時分秒件本便回人位%])"
    r"|(?:系統|路線|route)\s*\d+|\d+\s*系統"
    r"|(?<![a-z0-9])(?!p\d)[a-z]\d{1,2}(?![a-z0-9])"
)


def needs_history(question: str) -> bool:
    """True when a question points back at the conversation and names no route of its own.

    With history present the API sends such a question to the LLM stage with
    that history instead of the rules: a rule match would replay
    args fixed for some other question, so "その路線は？" would be answered
    about whichever route those args name. A question that names its own
    route gives the reference something in the question to point at
    ("22171の遅延とその推移").
    """
    text = _fold(question or "").strip()
    return bool(_ANAPHORA_RE.search(text)) and not _OWN_ROUTE_RE.search(text)


def _has_own_subject(question: str) -> bool:
    text = _fold(question)
    named = bool(_SUBJECT_RE.search(text) or _OWN_ROUTE_RE.search(text))
    return named and not needs_history(question)


def match_rules(question: str) -> RouterDecision | None:
    """Return the first matching rule's decision, or ``None``.

    The question is NFKC-normalised first, so full-width digits and Latin
    letters and half-width kana match the rules' plain spelling.
    """
    if not question:
        return None
    text = unicodedata.normalize("NFKC", question).strip()
    for rule in _RULES:
        m = rule.pattern.search(text)
        if m and not (rule.unless and rule.unless.search(text)):
            args = dict(rule.args)
            # Honor a captured count: if the rule carries an "n" arg and the
            # match captured an all-digit group, override the hardcoded n.
            if "n" in args:
                for g in m.groups():
                    if g is not None and g.isdigit():
                        args["n"] = int(g)
                        break
            return RouterDecision(
                tool=rule.tool,
                args=args,
                score=1.0,
                matched_pattern=rule.name,
            )
    return None


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()
