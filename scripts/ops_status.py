#!/usr/bin/env python3
"""The operations-status contract: one versioned, read-only status document shape
shared by every ops component (the VPS loop, GitHub, the Oracle crawler, and R2).

Items 119-123 each publish or collect one component's health (Oracle heartbeat,
VPS/loop collector, GitHub collector, storage metrics, and the ops status page
that assembles all of them). Without a shared contract, each would invent its
own state names, freshness math, and bounds -- and the page assembling them
would have no common ground to render against. This module is that shared
ground: it is data-only (a snapshot of what is true right now, never a command
or a write path) and versioned (`schema_version`) so a future incompatible
change can be detected by a consumer instead of silently misread.

Every component status is a `ComponentStatus`, carrying exactly:

- `component`: one of `COMPONENTS` (`vps_loop`, `github`, `oracle_crawler`, `r2`).
- `state`: one of `STATES`:
    - `healthy`  -- observed recently, age within the component's own
                    healthy threshold.
    - `degraded` -- observed recently, age past the healthy threshold but
                    within the stale threshold (still working, running late).
    - `stale`    -- age past the stale threshold (probably stopped, but no
                    explicit failure was reported).
    - `failed`   -- the component itself reported an explicit failure,
                    regardless of how recent that failure was.
    - `unknown`  -- health cannot be determined: no success has ever been
                    observed, the document's own timestamps are internally
                    inconsistent, or the reporting clock cannot be trusted
                    (see `classify_state`'s clock-skew handling).
- `observed_at`: when this snapshot was produced (timezone-aware UTC).
- `last_success_at`: when the component last succeeded, or `None` if it never
  has.
- `age_seconds`: `observed_at - last_success_at` in whole seconds, or `None`
  when it cannot be computed (no success yet, or `unknown` due to clock skew).
- `details`: a small, bounded, credential-free bag of component-specific
  facts (see `validate_details`). Never raw logs, tracebacks, secrets, or a
  per-object listing -- those are unbounded by construction and do not
  belong in a status snapshot.

`JSON_SCHEMA` mirrors these rules in portable JSON Schema form (draft
2020-12) for any non-Python producer (e.g. a future Oracle-side script) to
read as documentation; `validate_document` is the authoritative enforcement
of the same rules, usable directly on a parsed JSON dict without going
through the dataclass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


class OpsStatusError(ValueError):
    """Raised when a status document violates the operations-status contract."""


SCHEMA_VERSION = 1

COMPONENTS: frozenset[str] = frozenset({"vps_loop", "github", "oracle_crawler", "r2"})

STATES: frozenset[str] = frozenset({"healthy", "degraded", "stale", "failed", "unknown"})

# How far a document's own `observed_at` may sit ahead of the validating
# process's wall clock before it is treated as untrustworthy rather than
# genuinely fresh. Bounds ordinary NTP drift between machines (Oracle, the
# VPS, GitHub-reported timestamps) while still catching a badly-skewed clock
# reporting a future timestamp.
DEFAULT_MAX_CLOCK_SKEW_SECONDS = 300.0

MAX_DETAIL_KEYS = 20
MAX_DETAIL_KEY_LENGTH = 64
MAX_DETAIL_STRING_LENGTH = 200
MAX_DETAIL_LIST_LENGTH = 10
# The one enforced ceiling on the whole serialized document (fixed fields --
# component, state, two timestamps, schema_version, age_seconds -- plus
# `details`). `validate_details` only bounds `details`' shape (key count,
# nesting, string/list length); it does NOT also cap total bytes, so filling
# every key to its per-field limit is a real, independently testable way to
# hit this ceiling rather than being blocked earlier by a redundant check.
# Small enough that a status snapshot can never grow into a log dump.
MAX_PAYLOAD_BYTES = 4096

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Substrings checked case-insensitively against every `details` key. A
# substring match (not just exact) catches `api_key`, `apikey`, `secret_ref`,
# etc. without enumerating every spelling.
_FORBIDDEN_KEY_SUBSTRINGS = (
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "apikey",
    "api_key",
    "privatekey",
    "private_key",
    "authorization",
    "auth_header",
    "cookie",
    "sshkey",
    "ssh_key",
    "accesskey",
    "access_key",
)
# Exact-key matches for the "no full logs" rule -- these are legitimate words
# that would false-positive as substrings of something else (e.g. "log" is a
# substring of "catalog"), so they are checked as whole keys only.
_FORBIDDEN_KEY_EXACT = frozenset(
    {"log", "logs", "traceback", "stacktrace", "stack_trace", "raw_log", "full_log", "stdout", "stderr"}
)

JSON_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://transit-app.internal/schemas/ops-status/v1",
    "title": "Operations component status",
    "description": __doc__,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "component",
        "state",
        "observed_at",
        "last_success_at",
        "age_seconds",
        "details",
    ],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "component": {"type": "string", "enum": sorted(COMPONENTS)},
        "state": {"type": "string", "enum": sorted(STATES)},
        "observed_at": {"type": "string", "format": "date-time"},
        "last_success_at": {"type": ["string", "null"], "format": "date-time"},
        "age_seconds": {"type": ["integer", "null"], "minimum": 0},
        "details": {
            "type": "object",
            "maxProperties": MAX_DETAIL_KEYS,
            "additionalProperties": {
                "type": ["string", "number", "boolean", "null", "array"],
                "maxLength": MAX_DETAIL_STRING_LENGTH,
                "items": {"type": ["string", "number", "boolean", "null"]},
                "maxItems": MAX_DETAIL_LIST_LENGTH,
            },
        },
    },
}


@dataclass(frozen=True)
class ComponentStatus:
    """One component's status snapshot. See the module docstring for field semantics."""

    component: str
    state: str
    observed_at: datetime
    last_success_at: datetime | None
    age_seconds: int | None
    details: Mapping[str, object]
    schema_version: int = SCHEMA_VERSION


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    """Reject a naive datetime: clock-skew handling is meaningless without a known offset."""

    if value.tzinfo is None:
        raise OpsStatusError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def compute_age_seconds(observed_at: datetime, last_success_at: datetime | None) -> int | None:
    """`observed_at - last_success_at` in whole, non-negative seconds, or `None` if unknown.

    Negative results (a `last_success_at` reported after `observed_at`, which
    should never happen within one honestly-produced document) clamp to 0
    rather than propagating a nonsensical negative age -- `classify_state`
    treats that same inconsistency as grounds for `unknown` before this
    function is even reached in the normal path, but callers using this
    helper standalone still get a safe value.
    """

    if last_success_at is None:
        return None
    observed_at = _require_utc(observed_at, field_name="observed_at")
    last_success_at = _require_utc(last_success_at, field_name="last_success_at")
    delta = (observed_at - last_success_at).total_seconds()
    return max(0, round(delta))


def classify_state(
    *,
    observed_at: datetime,
    last_success_at: datetime | None,
    healthy_max_age_seconds: float,
    stale_max_age_seconds: float,
    reported_failure: bool = False,
    now: datetime | None = None,
    max_clock_skew_seconds: float = DEFAULT_MAX_CLOCK_SKEW_SECONDS,
) -> tuple[str, int | None]:
    """Derive `(state, age_seconds)` from raw facts, per the module docstring's state semantics.

    `healthy_max_age_seconds` and `stale_max_age_seconds` are supplied by the
    caller (each component has its own cadence -- an hourly VPS loop tick and
    a per-minute R2 sync do not share one threshold), not fixed here.

    Clock skew is handled two ways, both resolving to `unknown` rather than a
    misleading fresh/stale verdict:
    - `observed_at` sits more than `max_clock_skew_seconds` ahead of `now`
      (the validating process's own clock) -- the reporting clock cannot be
      trusted.
    - `last_success_at` sits after `observed_at` by more than
      `max_clock_skew_seconds` -- the document is internally inconsistent
      (its own two timestamps disagree about the order of events).
    """

    if stale_max_age_seconds < healthy_max_age_seconds:
        raise OpsStatusError("stale_max_age_seconds must be >= healthy_max_age_seconds")

    observed_at = _require_utc(observed_at, field_name="observed_at")
    now = _require_utc(now, field_name="now") if now is not None else datetime.now(timezone.utc)

    if (observed_at - now).total_seconds() > max_clock_skew_seconds:
        return "unknown", None

    if last_success_at is not None:
        last_success_at = _require_utc(last_success_at, field_name="last_success_at")
        if (last_success_at - observed_at).total_seconds() > max_clock_skew_seconds:
            return "unknown", None

    if reported_failure:
        return "failed", compute_age_seconds(observed_at, last_success_at)

    if last_success_at is None:
        return "unknown", None

    age_seconds = compute_age_seconds(observed_at, last_success_at)
    assert age_seconds is not None
    if age_seconds <= healthy_max_age_seconds:
        return "healthy", age_seconds
    if age_seconds <= stale_max_age_seconds:
        return "degraded", age_seconds
    return "stale", age_seconds


def validate_details(details: object) -> None:
    """Enforce the bounded, credential-free `details` contract.

    Rejects: a non-`dict` value, too many keys, an oversized or malformed
    key, a key that looks like it carries a credential or a full log/
    traceback, a nested object (only flat scalars and scalar lists are
    allowed -- no per-object listings), and an oversized string or list.
    Total serialized size is bounded separately, at the whole-document level
    (see `MAX_PAYLOAD_BYTES`), not here.
    """

    if not isinstance(details, dict):
        raise OpsStatusError("details must be an object")
    if len(details) > MAX_DETAIL_KEYS:
        raise OpsStatusError(f"details has {len(details)} keys, more than the {MAX_DETAIL_KEYS} allowed")

    for key, value in details.items():
        if not isinstance(key, str) or not _NAME_RE.match(key):
            raise OpsStatusError(f"details key {key!r} is not a bounded lowercase_snake_case identifier")
        if len(key) > MAX_DETAIL_KEY_LENGTH:
            raise OpsStatusError(f"details key {key!r} exceeds {MAX_DETAIL_KEY_LENGTH} characters")
        lowered = key.lower()
        if lowered in _FORBIDDEN_KEY_EXACT:
            raise OpsStatusError(f"details key {key!r} looks like raw log/traceback content, which is forbidden")
        if any(substring in lowered for substring in _FORBIDDEN_KEY_SUBSTRINGS):
            raise OpsStatusError(f"details key {key!r} looks like it carries credential material, which is forbidden")

        if isinstance(value, (list, tuple)):
            if len(value) > MAX_DETAIL_LIST_LENGTH:
                raise OpsStatusError(f"details[{key!r}] has {len(value)} items, more than {MAX_DETAIL_LIST_LENGTH}")
            for item in value:
                _validate_detail_scalar(item, key=key)
        else:
            _validate_detail_scalar(value, key=key)


def _validate_detail_scalar(value: object, *, key: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return
    if isinstance(value, str):
        if len(value) > MAX_DETAIL_STRING_LENGTH:
            raise OpsStatusError(f"details[{key!r}] string exceeds {MAX_DETAIL_STRING_LENGTH} characters")
        return
    raise OpsStatusError(
        f"details[{key!r}] has unsupported type {type(value).__name__}; only flat scalars/scalar lists are allowed"
    )


def validate_component_status(status: ComponentStatus) -> None:
    """Enforce every contract rule on an already-constructed `ComponentStatus`."""

    if status.schema_version != SCHEMA_VERSION:
        raise OpsStatusError(f"unsupported schema_version {status.schema_version} (expected {SCHEMA_VERSION})")
    if status.component not in COMPONENTS:
        raise OpsStatusError(f"unknown component {status.component!r} (expected one of {sorted(COMPONENTS)})")
    if status.state not in STATES:
        raise OpsStatusError(f"unknown state {status.state!r} (expected one of {sorted(STATES)})")
    _require_utc(status.observed_at, field_name="observed_at")
    if status.last_success_at is not None:
        _require_utc(status.last_success_at, field_name="last_success_at")
    if status.age_seconds is not None and status.age_seconds < 0:
        raise OpsStatusError("age_seconds must be >= 0")
    validate_details(status.details)

    document = to_json_dict(status)
    size = len(json.dumps(document, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise OpsStatusError(f"status document is {size} bytes, more than the {MAX_PAYLOAD_BYTES}-byte limit")


def build_status(
    *,
    component: str,
    observed_at: datetime,
    last_success_at: datetime | None,
    healthy_max_age_seconds: float,
    stale_max_age_seconds: float,
    reported_failure: bool = False,
    details: Mapping[str, object] | None = None,
    now: datetime | None = None,
    max_clock_skew_seconds: float = DEFAULT_MAX_CLOCK_SKEW_SECONDS,
) -> ComponentStatus:
    """Build and validate a `ComponentStatus`, deriving `state`/`age_seconds` via `classify_state`."""

    state, age_seconds = classify_state(
        observed_at=observed_at,
        last_success_at=last_success_at,
        healthy_max_age_seconds=healthy_max_age_seconds,
        stale_max_age_seconds=stale_max_age_seconds,
        reported_failure=reported_failure,
        now=now,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )
    status = ComponentStatus(
        component=component,
        state=state,
        observed_at=_require_utc(observed_at, field_name="observed_at"),
        last_success_at=(
            _require_utc(last_success_at, field_name="last_success_at") if last_success_at is not None else None
        ),
        age_seconds=age_seconds,
        details=dict(details or {}),
    )
    validate_component_status(status)
    return status


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise OpsStatusError(f"{field_name} is not a valid ISO 8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise OpsStatusError(f"{field_name} must include a UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def to_json_dict(status: ComponentStatus) -> dict:
    """Render a `ComponentStatus` as a plain, JSON-serializable dict matching `JSON_SCHEMA`."""

    return {
        "schema_version": status.schema_version,
        "component": status.component,
        "state": status.state,
        "observed_at": _isoformat(status.observed_at),
        "last_success_at": _isoformat(status.last_success_at),
        "age_seconds": status.age_seconds,
        "details": dict(status.details),
    }


def from_json_dict(data: Mapping[str, object]) -> ComponentStatus:
    """Parse and validate a plain dict (e.g. from `json.load`) into a `ComponentStatus`."""

    validate_document(data)
    last_success_raw = data.get("last_success_at")
    status = ComponentStatus(
        schema_version=data["schema_version"],  # type: ignore[arg-type]
        component=data["component"],  # type: ignore[arg-type]
        state=data["state"],  # type: ignore[arg-type]
        observed_at=_parse_timestamp(data["observed_at"], field_name="observed_at"),  # type: ignore[arg-type]
        last_success_at=(
            _parse_timestamp(last_success_raw, field_name="last_success_at")  # type: ignore[arg-type]
            if last_success_raw is not None
            else None
        ),
        age_seconds=data.get("age_seconds"),  # type: ignore[arg-type]
        details=data["details"],  # type: ignore[arg-type]
    )
    validate_component_status(status)
    return status


_REQUIRED_FIELDS = ("schema_version", "component", "state", "observed_at", "last_success_at", "age_seconds", "details")


def validate_document(data: object) -> None:
    """Validate a raw, already-parsed JSON dict against the contract before any dataclass exists.

    This is the entry point a non-Python producer's output should be checked
    against (e.g. via this module's `--validate` CLI), and the one this
    module's own `from_json_dict` uses internally.
    """

    if not isinstance(data, dict):
        raise OpsStatusError("status document must be a JSON object")

    missing = [name for name in _REQUIRED_FIELDS if name not in data]
    if missing:
        raise OpsStatusError(f"status document is missing required field(s): {', '.join(missing)}")
    extra = sorted(set(data) - set(_REQUIRED_FIELDS))
    if extra:
        raise OpsStatusError(f"status document has unexpected field(s): {', '.join(extra)}")

    if data["schema_version"] != SCHEMA_VERSION:
        raise OpsStatusError(f"unsupported schema_version {data['schema_version']!r} (expected {SCHEMA_VERSION})")
    if data["component"] not in COMPONENTS:
        raise OpsStatusError(f"unknown component {data['component']!r} (expected one of {sorted(COMPONENTS)})")
    if data["state"] not in STATES:
        raise OpsStatusError(f"unknown state {data['state']!r} (expected one of {sorted(STATES)})")
    if not isinstance(data["observed_at"], str):
        raise OpsStatusError("observed_at must be a string")
    if data["last_success_at"] is not None and not isinstance(data["last_success_at"], str):
        raise OpsStatusError("last_success_at must be a string or null")
    age_seconds = data["age_seconds"]
    age_seconds_invalid = age_seconds is not None and (
        not isinstance(age_seconds, int) or isinstance(age_seconds, bool) or age_seconds < 0
    )
    if age_seconds_invalid:
        raise OpsStatusError("age_seconds must be a non-negative integer or null")

    _parse_timestamp(data["observed_at"], field_name="observed_at")
    if data["last_success_at"] is not None:
        _parse_timestamp(data["last_success_at"], field_name="last_success_at")

    validate_details(data["details"])

    size = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise OpsStatusError(f"status document is {size} bytes, more than the {MAX_PAYLOAD_BYTES}-byte limit")


def build_bundle(statuses: Mapping[str, ComponentStatus], *, generated_at: datetime | None = None) -> dict:
    """Assemble one or more component statuses into a single versioned page-ready document.

    Producers (items 119-122) each publish their own `ComponentStatus`
    independently; this only groups already-validated statuses for a
    consumer (item 123's status page) that wants one document instead of N.
    Every key in `statuses` must equal its own value's `component`, so a
    caller cannot accidentally file a status snapshot under the wrong name.
    """

    for name, status in statuses.items():
        validate_component_status(status)
        if name != status.component:
            raise OpsStatusError(f"bundle key {name!r} does not match its status's own component {status.component!r}")

    if generated_at is not None:
        generated_at = _require_utc(generated_at, field_name="generated_at")
    else:
        generated_at = datetime.now(timezone.utc)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _isoformat(generated_at),
        "components": {name: to_json_dict(status) for name, status in statuses.items()},
    }
    size = len(json.dumps(bundle, separators=(",", ":")).encode("utf-8"))
    max_bundle_bytes = MAX_PAYLOAD_BYTES * len(COMPONENTS) + 512
    if size > max_bundle_bytes:
        raise OpsStatusError(f"status bundle is {size} bytes, more than the {max_bundle_bytes}-byte limit")
    return bundle


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: `--validate FILE` checks one status document against the contract."""

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate", type=Path, required=True, help="Path to a JSON status document to validate")
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.validate.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"ERROR: cannot read {args.validate}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: {args.validate} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        validate_document(data)
    except OpsStatusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {args.validate} matches the operations-status contract (schema_version {SCHEMA_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
