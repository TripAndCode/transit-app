"""Live-LLM regression test.

Replays ``golden_set.jsonl`` against the running API and scores
tool-selection accuracy. Off by default — set ``RUN_LLM_EVAL=1`` plus a
valid ``GROQ_API_KEY`` to run. Requires the dev API to be reachable at
``EVAL_API_BASE`` (default ``http://localhost:8000``) with at least one
seeded agency (``EVAL_AGENCY_ID``, default ``1``).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import httpx
import pytest

GOLDEN = Path(__file__).parent / "golden_set.jsonl"
EVAL_API_BASE = os.environ.get("EVAL_API_BASE", "http://localhost:8000")
EVAL_AGENCY_ID = int(os.environ.get("EVAL_AGENCY_ID", "1"))
SCORE_TARGET = float(os.environ.get("EVAL_SCORE_TARGET", "0.85"))

pytestmark = [
    pytest.mark.requires_groq_key,
    pytest.mark.skipif(
        os.environ.get("RUN_LLM_EVAL") != "1",
        reason="RUN_LLM_EVAL=1 not set",
    ),
]


def _load_cases():
    with GOLDEN.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _score(expected_tool, expected_args, actual_tool, actual_args):
    if expected_tool is None:
        return 1.0 if actual_tool is None else 0.0
    if actual_tool != expected_tool:
        return 0.0
    for k, want in (expected_args or {}).items():
        got = (actual_args or {}).get(k)
        if isinstance(want, str) and isinstance(got, str):
            if want.lower() != got.lower():
                return 0.5
        elif got != want:
            return 0.5
    return 1.0


def test_golden_set_aggregate_score():
    cases = list(_load_cases())
    assert cases, "golden_set.jsonl is empty"
    scores = []
    failures = []
    stage_counts: Counter = Counter()
    with httpx.Client(base_url=EVAL_API_BASE, timeout=60.0) as client:
        for case in cases:
            resp = client.post(
                f"/api/{EVAL_AGENCY_ID}/ask",
                json={"question": case["question"]},
                headers={"Origin": EVAL_API_BASE},
            )
            resp.raise_for_status()
            data = resp.json()
            tc = data.get("tool_call") or {}
            actual_tool = tc.get("name")
            actual_args = tc.get("arguments") or {}
            stage = data.get("router_stage") or "llm"
            stage_counts[stage] += 1
            s = _score(
                case["expected_tool"],
                case.get("expected_args"),
                actual_tool,
                actual_args,
            )
            scores.append(s)
            if s < 1.0:
                failures.append((case["id"], case["question"], case["expected_tool"], actual_tool, s))

    agg = sum(scores) / len(scores)
    stage_report = " ".join(f"{k}={v}" for k, v in sorted(stage_counts.items()))
    print(f"\nstage tally: {stage_report}  aggregate={agg:.2f} target={SCORE_TARGET:.2f}")

    if agg < SCORE_TARGET:
        report = "\n".join(
            f"  {cid:20s} score={s:.1f}  expected={want}  got={got}  q={q!r}" for cid, q, want, got, s in failures
        )
        pytest.fail(
            f"golden-set aggregate {agg:.2f} below target {SCORE_TARGET:.2f}\n"
            f"stage tally: {stage_report}\n"
            f"failing cases ({len(failures)}/{len(cases)}):\n{report}"
        )


@pytest.mark.requires_groq_key
@pytest.mark.skipif(os.environ.get("RUN_LLM_EVAL") != "1", reason="RUN_LLM_EVAL=1 not set")
def test_followup_pagination_two_turns():
    """Turn 1 lists stops; turn 2 ('次の50件' with turn-1 in history) paginates."""
    with httpx.Client(base_url=EVAL_API_BASE, timeout=60.0) as client:
        r1 = client.post(
            f"/api/{EVAL_AGENCY_ID}/ask",
            json={"question": "停留所はいくつ？"},
            headers={"Origin": EVAL_API_BASE},
        )
        r1.raise_for_status()
        d1 = r1.json()
        tc1 = d1.get("tool_call") or {}
        assert tc1.get("name") == "describe_data", f"turn-1 tool was {tc1.get('name')}"

        history = [{"question": "停留所はいくつ？", "tool": tc1.get("name"), "args": tc1.get("arguments")}]
        r2 = client.post(
            f"/api/{EVAL_AGENCY_ID}/ask",
            json={"question": "次の50件", "history": history},
            headers={"Origin": EVAL_API_BASE},
        )
        r2.raise_for_status()
        d2 = r2.json()
        tc2 = d2.get("tool_call") or {}
        assert tc2.get("name") == "describe_data", f"turn-2 tool was {tc2.get('name')}"
        assert (tc2.get("arguments") or {}).get("offset"), "turn-2 should paginate with an offset"
