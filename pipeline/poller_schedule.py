"""When the standalone poller runs its background steps, as pure functions.

Three kinds of step:
- ``utc``: the first cycle of each UTC day (and of a start) packs the closed
  days, then promotes, ingests and analyzes every archived agency;
- ``jst``: the first cycle of each JST day promotes and analyzes;
- ``retry``: a stage that failed runs again once its wait is over.

Every (kind, agency, stage) that fails keeps its own retry budget, which its
kind's next day resets: one agency's failures never spend another's, and a
JST step that fails after the UTC step used its budget is still retried. A
stage skipped for a cause no retry heals has no budget: it waits for its
kind's next day at once.
:func:`decide` picks the step to start, :func:`settle` folds its result in,
and :func:`heartbeat` says what that result tells a dead-man's switch; none
reads a clock or touches a thread, so a table test covers them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

RETRY_AFTER = timedelta(minutes=15)
MAX_RETRIES = 8

UTC_STAGES = ("pack", "promote", "ingest", "analyze")
JST_STAGES = ("promote", "analyze")

_JST = ZoneInfo("Asia/Tokyo")

# (kind of the step the stage belongs to, agency_id, stage)
Key = tuple[str, int, str]


@dataclass(frozen=True)
class Pending:
    """A stage that failed: retried at *due* while *retries* < MAX_RETRIES."""

    retries: int
    due: datetime


@dataclass(frozen=True)
class State:
    utc_day: date | None = None
    jst_day: date | None = None
    pending: Mapping[Key, Pending] = field(default_factory=lambda: MappingProxyType({}))


def _days(now: datetime) -> tuple[date, date]:
    return now.astimezone(timezone.utc).date(), now.astimezone(_JST).date()


def _followups(keys: Iterable[Key]) -> frozenset[Key]:
    """A retried stage brings the stages that consume its result."""
    out = set(keys)
    for kind, aid, stage in list(out):
        if stage == "pack":
            out.add((kind, aid, "ingest"))
        if stage in ("pack", "promote", "ingest"):
            out.add((kind, aid, "analyze"))
    return frozenset(out)


def decide(now: datetime, agencies: Iterable[int], state: State) -> tuple[str | None, frozenset[Key], State]:
    """The step to start now, its stages, and the state once it has started.
    ``(None, ∅, state)`` when nothing is due."""
    utc_today, jst_today = _days(now)
    agencies = sorted(agencies)
    if state.utc_day != utc_today:
        pending = {k: v for k, v in state.pending.items() if k[0] != "utc"}
        plan = frozenset(("utc", aid, stage) for aid in agencies for stage in UTC_STAGES)
        return "utc", plan, State(utc_today, jst_today, MappingProxyType(pending))
    if state.jst_day != jst_today:
        pending = {k: v for k, v in state.pending.items() if k[0] != "jst"}
        plan = frozenset(("jst", aid, stage) for aid in agencies for stage in JST_STAGES)
        return "jst", plan, replace(state, jst_day=jst_today, pending=MappingProxyType(pending))
    due = [k for k, p in state.pending.items() if p.retries < MAX_RETRIES and p.due <= now]
    if due:
        return "retry", _followups(due) - set(exhausted(state)), state
    return None, frozenset(), state


def settle(
    now: datetime,
    state: State,
    ran: Iterable[Key],
    failed: Iterable[tuple[int, str]],
    skipped: Iterable[tuple[int, str]] = (),
) -> State:
    """Fold a step's outcome in: each stage that ran and failed waits
    RETRY_AFTER with one more retry spent if it was already pending; each
    that was skipped has spent every retry; each that ran and succeeded is
    no longer pending."""
    failed, skipped = set(failed), set(skipped)
    pending = dict(state.pending)
    for key in ran:
        if (key[1], key[2]) in skipped:
            pending[key] = Pending(retries=MAX_RETRIES, due=now)
        elif (key[1], key[2]) in failed:
            before = pending.get(key)
            pending[key] = Pending(retries=before.retries + 1 if before else 0, due=now + RETRY_AFTER)
        else:
            pending.pop(key, None)
    return replace(state, pending=MappingProxyType(pending))


def exhausted(state: State) -> list[Key]:
    return sorted(k for k, p in state.pending.items() if p.retries >= MAX_RETRIES)


def newly_exhausted(before: State, after: State) -> list[Key]:
    return sorted(set(exhausted(after)) - set(exhausted(before)))


def heartbeat(before: State, after: State) -> str | None:
    """What a settled step reports: ``"fail"`` when a stage has just spent its
    last retry or been skipped, ``"ok"`` once no stage is failing, else
    nothing. A spent stage stays pending until its kind's next day, so no
    ``"ok"`` follows a ``"fail"`` until that day's step settles clean."""
    if newly_exhausted(before, after):
        return "fail"
    return None if after.pending else "ok"
