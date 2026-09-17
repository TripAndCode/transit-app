"""Unit tests for _nn_distance_for_tool helper (Stage 3 confidence plumbing)."""

from types import SimpleNamespace as N


def test_nn_distance_for_tool_picks_smallest_same_tool():
    """Returns the smallest distance among examples whose tool matches; None if no match."""
    from pipeline.query.chat import _nn_distance_for_tool

    examples = [
        N(tool="top_n", distance=0.30),
        N(tool="time_series", distance=0.10),
        N(tool="top_n", distance=0.20),
    ]
    assert _nn_distance_for_tool(examples, "top_n") == 0.20
    assert _nn_distance_for_tool(examples, "time_series") == 0.10
    assert _nn_distance_for_tool(examples, "compare_segments") is None
    assert _nn_distance_for_tool([], "top_n") is None
