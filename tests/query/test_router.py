import pytest

from pipeline.query.router import RouterDecision, _match_rules


@pytest.mark.parametrize(
    "question,expected_tool,expected_kind",
    [
        ("どんな路線がデータにあるの？", "describe_data", "routes"),
        ("路線一覧を見せて", "describe_data", "routes"),
        ("いつからのデータ？", "describe_data", "date_range"),
        ("最新のデータはいつ？", "describe_data", "date_range"),
        ("何件くらいの観測がある？", "describe_data", "date_range"),
        ("停留所はいくつ？", "describe_data", "stops"),
        ("何社の事業者？", "describe_data", "agencies"),
        ("全体の概要を", "describe_data", "overview"),
        ("計算できる指標は？", "describe_data", "metrics"),
        ("サンプル数の多い系統", "describe_data", "sample_counts"),
    ],
)
def test_rule_meta_dispatch(question, expected_tool, expected_kind):
    decision = _match_rules(question)
    assert decision is not None
    assert decision.tool == expected_tool
    assert decision.args.get("kind") == expected_kind
    assert decision.stage == "rules"


@pytest.mark.parametrize(
    "question",
    [
        "雨の日の遅延は？",
        "なぜ最近遅れているの？",
        "全国平均と比べて？",
    ],
)
def test_rule_no_match(question):
    """Questions outside the rule set return None — fall through to Stage 2."""
    assert _match_rules(question) is None


def test_rule_decision_records_pattern_name():
    decision = _match_rules("どんな路線がある？")
    assert decision is not None
    assert decision.matched_pattern  # non-empty rule name


def test_all_rules_map_to_known_tools():
    """Every rule's `tool` must exist in the dispatcher's _HANDLERS."""
    from pipeline.query.router import _RULES
    from pipeline.query.tools import _HANDLERS
    known = set(_HANDLERS.keys())
    bad = [r.name for r in _RULES if r.tool not in known]
    assert not bad, f"rules with unknown tool name: {bad}"
