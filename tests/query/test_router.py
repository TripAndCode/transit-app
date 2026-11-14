import pytest

from pipeline.query.router import (
    is_follow_up,
    match_rules,
    needs_history,
    needs_previous_turn,
)


@pytest.mark.parametrize(
    "question,expected_tool,expected_kind",
    [
        ("どんな路線がデータにあるの？", "describe_data", "routes"),
        ("路線一覧を見せて", "describe_data", "routes"),
        ("いつからのデータ？", "describe_data", "date_range"),
        ("いつ頃からのデータ？", "describe_data", "date_range"),
        ("最新のデータはいつ？", "describe_data", "date_range"),
        ("何件くらいの観測がある？", "describe_data", "date_range"),
        ("停留所はいくつ？", "describe_data", "stops"),
        ("何社の事業者？", "describe_data", "agencies"),
        ("全体の概要を", "describe_data", "overview"),
        ("計算できる指標は？", "describe_data", "metrics"),
        ("サンプル数の多い系統", "describe_data", "sample_counts"),
        ("サンプル数が多い路線の一覧", "describe_data", "sample_counts"),
        # A date's digits are not a route reference.
        ("2025年の全体の概要を", "describe_data", "overview"),
        ("2024年10月のデータセットの概要", "describe_data", "overview"),
        ("2025/10の全体の概要を", "describe_data", "overview"),
        ("2025-10-01のデータセットの概要", "describe_data", "overview"),
        ("20251001の概要を", "describe_data", "overview"),
        # An on-time word does not keep a date-range question from its rule.
        ("定時率のデータはいつから？", "describe_data", "date_range"),
        ("定時率は何件くらいの観測がある？", "describe_data", "date_range"),
    ],
)
def test_rule_meta_dispatch(question, expected_tool, expected_kind):
    decision = match_rules(question)
    assert decision is not None
    assert decision.tool == expected_tool
    assert decision.args.get("kind") == expected_kind


@pytest.mark.parametrize(
    "question",
    [
        "雨の日の遅延は？",
        "なぜ最近遅れているの？",
        "全国平均と比べて？",
    ],
)
def test_rule_no_match(question):
    """Questions outside the rule set return None — fall through to the LLM stage."""
    assert match_rules(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "22171はいつから遅れ始めた?",
        "いつも混む時間帯から教えて",
        "最新のデータで22171の遅延は?",
        "何分遅れている路線がある?",
        "系統ごとの遅延一覧",
        "路線22171の停留所一覧",
        "停留所ごとの遅延一覧",
        "22171の遅延が多い停留所TOP5",
        "停留所別の定時率ワースト5",
        "22171の遅延の概要を教えて",
        "路線22171の概要を教えて",
    ],
)
def test_analytic_questions_are_not_answered_by_a_meta_or_route_ranking_rule(question):
    """These ask about delays on a route or stop, not what the dataset holds
    or the agency-wide route ranking, so no rule may answer them."""
    assert match_rules(question) is None


def test_oos_guard_routes_to_capabilities():
    for q in ["今日の天気は？", "運賃はいくら？", "事故情報を教えて"]:
        d = match_rules(q)
        assert d is not None and d.tool == "capabilities", q


def test_metrics_rule_does_not_overfire_on_definition():
    # A definition question should NOT hit meta-metrics
    d = match_rules("定時率という指標の意味を教えて")
    # acceptable: either None (falls through) or capabilities — but NOT describe_data/metrics
    assert d is None or d.tool != "describe_data"


def test_rule_5min_not_shadowed_by_worst():
    d = match_rules("5分以上の遅れが多い系統TOP10")
    assert d is not None
    assert d.tool == "top_n"
    assert d.args["metric"] == "worst_5min"


def test_rule_honors_captured_n():
    d = match_rules("遅延ワースト3")
    assert d is not None
    assert d.tool == "top_n"
    assert d.args["n"] == 3


def test_rule_default_n_when_no_digit():
    d = match_rules("遅延ワースト")
    assert d is not None
    assert d.args["n"] == 10


@pytest.mark.parametrize(
    "question, metric, best_first, n",
    [
        ("定時率が低い路線ランキング", "on_time_rate", False, 10),
        ("定時率ワーストランキング", "on_time_rate", False, 10),
        ("定時率のワーストは？", "on_time_rate", False, 10),
        ("定時率が悪い路線TOP5", "on_time_rate", False, 5),
        ("定時率ワースト5", "on_time_rate", False, 5),
        ("定時率下位3", "on_time_rate", False, 3),
        ("定時率TOP10", "on_time_rate", None, 10),
        ("定時率TOP5", "on_time_rate", None, 5),
        ("定時率が高い路線TOP3", "on_time_rate", None, 3),
        ("定時率ランキング", "on_time_rate", None, 10),
        ("定時率TOP5の路線一覧", "on_time_rate", None, 5),
        ("定時率が高い路線の一覧", "on_time_rate", None, 10),
        ("5分超の遅延が多い路線TOP3", "worst_5min", None, 3),
        ("5分以上の遅れが多い系統TOP10", "worst_5min", None, 10),
        ("5分以上の遅れが多い系統", "worst_5min", None, 10),
        ("5分超の遅延ワースト5", "worst_5min", None, 5),
        ("5分を超える遅れが多い路線上位3", "worst_5min", None, 3),
        ("遅延が少ない路線TOP5", "avg_delay", True, 5),
        ("遅れが最も少ない路線ワースト", "avg_delay", True, 10),
        ("遅延ワースト3", "avg_delay", None, 3),
    ],
)
def test_ranking_rules_follow_the_asked_direction(question, metric, best_first, n):
    """A low/few phrasing asks for the other end of the ranking; best_first
    stays unset (the tool's own default) only for the default direction."""
    d = match_rules(question)
    assert d is not None and d.tool == "top_n"
    assert (d.args["metric"], d.args.get("best_first"), d.args["n"]) == (metric, best_first, n)


@pytest.mark.parametrize(
    "question, metric, n",
    [
        ("５分以上の遅れが多い系統", "worst_5min", 10),
        ("５分以上の遅れが多い系統ＴＯＰ１０", "worst_5min", 10),
        ("５分超の遅延ワースト３", "worst_5min", 3),
        ("遅延ﾜｰｽﾄ3", "avg_delay", 3),
        ("遅延ワースト５", "avg_delay", 5),
    ],
)
def test_rules_match_after_nfkc_normalisation(question, metric, n):
    """Full-width digits/latin and half-width kana fold to their canonical
    forms before the rules run, so they match like the plain spelling."""
    d = match_rules(question)
    assert d is not None and d.tool == "top_n"
    assert (d.args["metric"], d.args["n"]) == (metric, n)


def test_full_width_threshold_other_than_five_still_goes_to_the_llm():
    assert match_rules("１５分以上の遅れが多い路線ＴＯＰ１０") is None
    assert match_rules("１５分を超える遅れが多い路線上位３") is None


@pytest.mark.parametrize(
    "question",
    [
        "5分以上の遅れが少ない路線TOP10",
        "5分以上の遅れが短い系統TOP10",
        # A threshold other than 5 minutes is not the worst_5min metric (nor an
        # average-delay ranking), so it goes to the LLM rather than a wrong tool.
        "15分以上の遅れが多い路線TOP10",
        "10分超の遅延が多い路線TOP5",
        "15分を超える遅れが多い路線TOP3",
        "2.5分以上の遅れワースト3",
        "15 分以上の遅れが多い路線TOP3",
        "少ない遅延のTOP",
        "低い定時率のランキング",
        "A系統の定時率が低い理由は？",
        "定時率が最低になった原因を教えて",
    ],
)
def test_ranking_rules_leave_an_order_they_cannot_express_to_the_llm(question):
    """worst_5min only ranks most-first, a few/low word ahead of the metric
    fits no inverse rule, and a low trait with no ranking marker is not a
    ranking request: no rule may answer these."""
    assert match_rules(question) is None


def test_rule_decision_records_pattern_name():
    decision = match_rules("どんな路線がある？")
    assert decision is not None
    assert decision.matched_pattern  # non-empty rule name


def test_all_rules_map_to_known_tools():
    """Every rule's `tool` must exist in the dispatcher's _HANDLERS."""
    from pipeline.query.router import _RULES
    from pipeline.query.tools import _HANDLERS

    known = set(_HANDLERS.keys())
    bad = [r.name for r in _RULES if r.tool not in known]
    assert not bad, f"rules with unknown tool name: {bad}"


@pytest.mark.parametrize(
    "q",
    [
        "もっと見せて",
        "もう少し",
        "続き",
        "次の50件",
        "次のページ",
        "2ページ目",
        "残り",
        "他には",
        "さらに",
        "前のと逆順で",
        "同じ条件で先月",
        "それを詳しく",
        "降順",
        "昇順",
        "逆に",
        "絞り込んで",
        "show me more",
        "next",
        "again",
        "reverse",
        "遅延の多い路線を降順で",
        "さらに定時率の低い路線",
        "別の路線と比較したい",
        "list routes by delay in reverse",
        # 別の after hiragana or at the start still reads as "another".
        "別の日",
        "それとは別の路線",
        "じゃあ別の系統",
        "また別の停留所",
        "全く別の時間帯",
        "全然別の路線",
    ],
)
def test_is_follow_up_true(q):
    assert is_follow_up(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "どんな路線がある？",
        "22171の遅延",
        "定時率TOP10",
        "停留所はいくつ？",
        "もっとも遅延が多い系統",
        "次の停留所は？",
        "前の停留所",
        "別府の路線",
        "same-day comparison",
        "next stop information",
        # 別 after a kanji or katakana word is a "by X" suffix, not "another".
        "月別の推移を教えて",
        "曜日別の遅延",
        "時間帯別の定時率",
        "路線別の遅延",
        "系統別の平均遅延",
        "停留所別の遅れ",
        "日々別の集計",
        "ルート別の比較",
        "ﾙｰﾄ別の比較",
        "特別の理由",
        "",
    ],
)
def test_is_follow_up_false(q):
    assert is_follow_up(q) is False


@pytest.mark.parametrize(
    "q",
    [
        "もっと見せて",
        "続き",
        "次の50件",
        "2ページ目",
        "前のと逆順で並べて",
        "同じ条件で先月",
        "それを詳しく",
        "さっきの続きを",
        "じゃあ別の系統で同じこと",
        "show me more",
        "降順",
        "さらに",
        "絞り込んで",
        "もっと",
        "次の10件",
        "逆に",
        "別の日",
        "平日に絞り込んで",
        "2025年に絞り込んで",
        "reverse",
        "in reverse",
        "別の便利な使い方",
        # Continuation wording needs the previous turn's list even beside a
        # subject.
        "もっと遅延の多い路線を見せて",
        "次の10件の路線",
        # The noun belongs to a reference back, not to the question.
        "その路線を降順で",
        "同じ路線を絞り込んで",
        "あの系統を並べ替えて",
        "その停留所をさらに",
        "それとは別の路線",
    ],
)
def test_needs_previous_turn_true(q):
    assert needs_previous_turn(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "遅延の多い路線を降順で",
        "さらに定時率の低い路線",
        "別の路線と比較したい",
        "22171の遅延を降順で",
        "A1を別の系統と比較",
        "停留所を遅延の多い順に並べ替えて",
        "バス停を遅れの大きい順に絞り込んで",
        "別の事業者の便と比較",
        "それぞれの路線を降順で",
        "定時率を昇順に並べ替え",
        "list routes by delay in reverse",
    ],
)
def test_refinement_wording_with_a_subject_of_its_own_does_not_need_a_previous_turn(q):
    """No rule answers these, but each names what to rank, filter or compare,
    so a first turn can still answer it."""
    assert match_rules(q) is None
    assert needs_previous_turn(q) is False
    assert needs_previous_turn(q, router_runs=False) is False


@pytest.mark.parametrize("q", ["月別の推移を教えて", "曜日別の遅延", "時間帯別の定時率", "ルート別の比較"])
def test_a_by_x_breakdown_does_not_need_a_previous_turn(q):
    assert needs_previous_turn(q) is False
    assert needs_previous_turn(q, router_runs=False) is False


@pytest.mark.parametrize(
    "q, routed, unrouted",
    [
        # A rule answers the continuation only when the router runs.
        ("遅延ワースト10の続き", False, True),
        # Refinement wording with a subject never needs the previous turn.
        ("平日に絞り込んで遅延ワースト10", False, False),
        # A back-reference always does.
        ("同じ条件で遅延ワースト10", True, True),
        ("降順", True, True),
    ],
)
def test_needs_previous_turn_without_the_router(q, routed, unrouted):
    assert needs_previous_turn(q) is routed
    assert needs_previous_turn(q, router_runs=False) is unrouted


@pytest.mark.parametrize(
    "q",
    [
        "同じ条件で遅延ワースト10",
        "さっきの定時率TOP5",
        "それを定時率ランキングで",
        "前の結果の遅延ワースト5",
    ],
)
def test_a_back_reference_needs_a_previous_turn_even_when_a_rule_matches(q):
    """A rule would answer the agency-wide ranking, not the result the
    question points back at, so a first turn cannot answer it."""
    assert match_rules(q) is not None
    assert needs_previous_turn(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "平日に絞り込んで遅延ワースト10",
        "定時率の低い順に並べ替えて",
        "逆に定時率が高い路線は",
        "定時率TOP10",
        "",
    ],
)
def test_needs_previous_turn_false(q):
    """Follow-up wording around a question a rule answers on its own
    does not need a prior turn."""
    assert needs_previous_turn(q) is False


@pytest.mark.parametrize(
    "q",
    [
        "その路線は？",
        "それの内訳は？",
        "この停留所の遅延は？",
        "そこの平日は？",
        "あの日の定時率は？",
        "あの系統はどう？",
        "その系統の休日は？",
        "それ以外の路線は？",
        "これの内訳を見せて",
        "あれの推移は？",
        "あそこの停留所は？",
        "そちらの系統は？",
        "こちらの停留所の遅延",
        "同じ路線の土日は？",
        "当該路線の推移",
        "上記の系統の定時率",
        "前述の停留所は？",
        "この系統の平均遅延を知りたい",
        # A count or rank is not a subject: this asks for the ranking within
        # the referenced result, not the agency-wide one a rule would answer.
        "それの遅延ワースト5",
        "その路線の定時率TOP10",
        # A year, a date or a stop number is not a route of the question's own.
        "2024年のその路線の定時率TOP10",
        "2025年のその系統の遅延ワースト5",
        "2026-10-20のその路線は？",
        "2026/10/20のそれの内訳は？",
        "停留所1234のその系統は？",
        # Nor is a duration, a count, or a token shaped unlike a route alias.
        "その系統で1200秒以上遅れた便",
        "上位1000件のその内訳",
        "PM5時以降のその系統",
        "No1のその路線",
        "What about THAT route?",
        "show me the same for weekends",
        "break it down by hour",
        "how did those stops do?",
        "compare this line on weekends",
        "and what about that?",
        "compare it with that",
        "how did these routes do?",
    ],
)
def test_needs_history_true(q):
    assert needs_history(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "",
        "   ",
        "定時率TOP10",
        "22171の遅延",
        # A route of its own gives the reference something in the question to
        # point at.
        "22171の遅延とその推移",
        "系統5とその定時率",
        "A1の遅延とその内訳",
        "２２１７１の遅延とその内訳",
        # Words that only contain a demonstrative.
        "その他の指標はある？",
        "路線そのものの遅延",
        "それほど遅れない路線は？",
        "これほど遅延が多い路線ワースト5",
        "あれほど遅れる路線は？",
        "それでも遅延が多い路線TOP10",
        "それにしても遅延が多い路線は？",
        "こちらでは何ができますか？",
        "今このシステムに何社入ってるの？",
        "路線ごとにそれぞれの定時率を見せて",
        "平日と休日、それとも土曜が遅い？",
        "遅延のおそれがある路線は？",
        "これまでの遅延の推移",
        "これからの運行は？",
        "この先も遅延は増える？",
        "この1週間の推移",
        "この数日の遅延",
        "この週末の遅延",
        "この冬の定時率",
        "この頃の遅延傾向は？",
        "このところ遅延は増えてる？",
        "このままだと遅延は増える？",
        "この間の遅延",
        "あのー、遅延ワースト10は？",
        "あのさ、遅延ワースト10は？",
        "あのね、遅延ワースト10は？",
        "データがあれば見せて",
        "そこそこ遅れる路線は？",
        "平日と土日は同じくらい？",
        "ここ1週間の推移",
        "routes that are late most often",
        "how was this week?",
        "same-day comparison",
        "next stop information",
        "p90 delay this month",
        "on-time rate for this week",
        "how about this month?",
        "how bad are delays these days?",
    ],
)
def test_needs_history_false(q):
    assert needs_history(q) is False
