"""No ClickHouse higher-order array function reads a query parameter array.

A lambda's call works on one array per row. A parameter array read inside it,
other than one element picked by a constant index, is copied per row, or per
element of each row's array, so the query's memory grows with its rows times
the parameter's length. A parameter array rows are matched against is joined
as rows instead (feed_quality.PEER_STRETCHES_CH_SQL); one read at known
positions is indexed by constants (timetable_sampling.stop_departures).

The scan reads each source file's own text, so it misses a lambda that only
meets the parameter through Python, such as a constant spliced into an
f-string from another name, and a parenthesis inside a SQL string literal
ends the call it sits in.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# A call whose first argument is a lambda, written as SQL or as SQL in an
# f-string; its opening parenthesis ends the match's name.
_LAMBDA_CALL = re.compile(r"\barray[A-Z]\w*\((?=\s*(?:\w+|\([\w\s,]+\))\s*->)")
_ARRAY_PARAMETER = re.compile(r"\{\{?\w+:Array(?=\()")
_CONSTANT_INDEX = re.compile(r"\}\}?\[\d+\]")
# Parameter arrays that grow with an agency's timetable revisions rather than
# with the rows read: one element per version in force over the read, or over
# the agency's whole history for a read with no date bound
# (ScheduleJoin.force_params).
ALLOWED = {
    ("pipeline/static_archive.py", "force_firsts:Array(Date32)"),
    ("pipeline/static_archive.py", "force_lasts:Array(Date32)"),
}


def _closing(text: str, opening: int) -> int:
    """The index just past the parenthesis that closes the one at *opening*."""
    depth = 0
    for at in range(opening, len(text)):
        depth += {"(": 1, ")": -1}.get(text[at], 0)
        if depth == 0:
            return at + 1
    return len(text)


def _sources() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [name for name in listed.stdout.split() if not name.startswith("tests/")]


def offenders(text: str) -> list[str]:
    """Each parameter array a lambda's call in *text* reads, but for one
    element of it at a constant index."""
    found = []
    for call in _LAMBDA_CALL.finditer(text):
        body = text[call.end() - 1 : _closing(text, call.end() - 1)]
        for parameter in _ARRAY_PARAMETER.finditer(body):
            end = _closing(body, parameter.end())
            if not _CONSTANT_INDEX.match(body, end):
                found.append(body[parameter.start() : end].lstrip("{"))
    return found


@pytest.mark.parametrize(
    "sql",
    [
        "arrayFilter(i -> has({{services:Array(Array(String))}}[i], service), range(1, 4))",
        "arrayJoin(arrayFilter(\n    i -> instant >= {{los:Array(Int64)}}[i] AND instant <= {{his:Array(Int64)}}[i],\n"
        "    arrayEnumerate({{los:Array(Int64)}})\n))",
        "arrayFilter((i, lo, hi) -> x >= lo AND x <= hi, arrayEnumerate({los:Array(Int64)}), {los:Array(Int64)})",
    ],
)
def test_a_parameter_array_a_lambda_call_reads_is_found(sql):
    assert offenders(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "arrayFilter(i -> i > 0, [if(has({{services:Array(Array(String))}}[1], service), 1, 0)])",
        "arrayJoin(arrayZip(arrayEnumerate({los:Array(Int64)}), {los:Array(Int64)}, {his:Array(Int64)}))",
        "arrayFilter((x, y) -> y - x > {{floor:UInt32}}, arrayPopBack(xs), arrayPopFront(xs))",
        "u.agency_id IN {peers:Array(UInt16)}",
    ],
)
def test_a_constant_index_a_scalar_or_no_lambda_is_not_an_offender(sql):
    assert offenders(sql) == []


def test_no_source_reads_a_parameter_array_inside_a_lambda_call():
    found = [
        (name, offender) for name in _sources() for offender in offenders((ROOT / name).read_text(encoding="utf-8"))
    ]
    assert [hit for hit in found if hit not in ALLOWED] == []


def test_every_allowed_parameter_is_still_read_inside_a_lambda_call():
    assert {
        (path, offender) for path, _ in ALLOWED for offender in offenders((ROOT / path).read_text(encoding="utf-8"))
    } >= ALLOWED
