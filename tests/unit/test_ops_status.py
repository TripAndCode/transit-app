"""Tests for the operations-status contract shared by ops components."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops_status.py"
SPEC = importlib.util.spec_from_file_location("ops_status", SCRIPT)
assert SPEC and SPEC.loader
ops_status = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ops_status
SPEC.loader.exec_module(ops_status)

OpsStatusError = ops_status.OpsStatusError

T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def utc(**offset_kwargs) -> datetime:
    return T0 + timedelta(**offset_kwargs)


# --- compute_age_seconds -----------------------------------------------------


def test_compute_age_seconds_none_without_last_success():
    assert ops_status.compute_age_seconds(T0, None) is None


def test_compute_age_seconds_normal_case():
    assert ops_status.compute_age_seconds(utc(seconds=90), T0) == 90


def test_compute_age_seconds_clamps_negative_to_zero():
    assert ops_status.compute_age_seconds(T0, utc(seconds=90)) == 0


def test_compute_age_seconds_rejects_naive_datetime():
    with pytest.raises(OpsStatusError, match="timezone-aware"):
        ops_status.compute_age_seconds(datetime(2026, 9, 11), T0)


# --- classify_state -----------------------------------------------------------


def test_classify_state_healthy_within_threshold():
    state, age = ops_status.classify_state(
        observed_at=utc(seconds=30),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=30),
    )
    assert (state, age) == ("healthy", 30)


def test_classify_state_degraded_between_thresholds():
    state, age = ops_status.classify_state(
        observed_at=utc(seconds=120),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=120),
    )
    assert (state, age) == ("degraded", 120)


def test_classify_state_stale_past_stale_threshold():
    state, age = ops_status.classify_state(
        observed_at=utc(seconds=400),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=400),
    )
    assert (state, age) == ("stale", 400)


def test_classify_state_healthy_boundary_is_inclusive():
    state, _ = ops_status.classify_state(
        observed_at=utc(seconds=60),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=60),
    )
    assert state == "healthy"


def test_classify_state_stale_boundary_is_inclusive_to_degraded():
    state, _ = ops_status.classify_state(
        observed_at=utc(seconds=300),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=300),
    )
    assert state == "degraded"


def test_classify_state_failed_overrides_age_even_when_fresh():
    state, age = ops_status.classify_state(
        observed_at=utc(seconds=5),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        reported_failure=True,
        now=utc(seconds=5),
    )
    assert (state, age) == ("failed", 5)


def test_classify_state_failed_with_no_prior_success():
    state, age = ops_status.classify_state(
        observed_at=T0,
        last_success_at=None,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        reported_failure=True,
        now=T0,
    )
    assert (state, age) == ("failed", None)


def test_classify_state_unknown_when_never_succeeded():
    state, age = ops_status.classify_state(
        observed_at=T0,
        last_success_at=None,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=T0,
    )
    assert (state, age) == ("unknown", None)


def test_classify_state_unknown_when_observed_at_ahead_of_now_beyond_skew():
    state, age = ops_status.classify_state(
        observed_at=utc(seconds=1000),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=T0,
        max_clock_skew_seconds=300,
    )
    assert (state, age) == ("unknown", None)


def test_classify_state_healthy_when_observed_at_ahead_of_now_within_skew():
    state, _ = ops_status.classify_state(
        observed_at=utc(seconds=100),
        last_success_at=utc(seconds=90),
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=T0,
        max_clock_skew_seconds=300,
    )
    assert state == "healthy"


def test_classify_state_unknown_when_last_success_after_observed_at_beyond_skew():
    state, age = ops_status.classify_state(
        observed_at=T0,
        last_success_at=utc(seconds=1000),
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=T0,
        max_clock_skew_seconds=300,
    )
    assert (state, age) == ("unknown", None)


def test_classify_state_rejects_inverted_thresholds():
    with pytest.raises(OpsStatusError, match="stale_max_age_seconds"):
        ops_status.classify_state(
            observed_at=T0,
            last_success_at=T0,
            healthy_max_age_seconds=300,
            stale_max_age_seconds=60,
            now=T0,
        )


def test_classify_state_rejects_naive_observed_at():
    with pytest.raises(OpsStatusError, match="timezone-aware"):
        ops_status.classify_state(
            observed_at=datetime(2026, 9, 11),
            last_success_at=None,
            healthy_max_age_seconds=60,
            stale_max_age_seconds=300,
        )


# --- validate_details ---------------------------------------------------------


def test_validate_details_accepts_flat_scalars_and_lists():
    ops_status.validate_details({"agencies_ok": 5, "note": "fine", "ratio": 0.5, "flag": True, "tags": ["a", "b"]})


def test_validate_details_rejects_non_dict():
    with pytest.raises(OpsStatusError, match="must be an object"):
        ops_status.validate_details(["not", "a", "dict"])


def test_validate_details_rejects_too_many_keys():
    details = {f"k{i}": i for i in range(ops_status.MAX_DETAIL_KEYS + 1)}
    with pytest.raises(OpsStatusError, match="more than"):
        ops_status.validate_details(details)


def test_validate_details_rejects_malformed_key():
    with pytest.raises(OpsStatusError, match="lowercase_snake_case"):
        ops_status.validate_details({"Not-Valid-Key": 1})


def test_validate_details_rejects_oversized_key():
    long_key = "k" * (ops_status.MAX_DETAIL_KEY_LENGTH + 1)
    with pytest.raises(OpsStatusError, match="lowercase_snake_case"):
        ops_status.validate_details({long_key: 1})


@pytest.mark.parametrize(
    "key",
    ["api_key", "apikey", "access_token", "user_password", "db_credential", "ssh_key", "authorization_header"],
)
def test_validate_details_rejects_credential_like_keys(key):
    with pytest.raises(OpsStatusError, match="credential"):
        ops_status.validate_details({key: "x"})


@pytest.mark.parametrize("key", ["log", "logs", "traceback", "stacktrace", "stdout", "stderr"])
def test_validate_details_rejects_log_like_keys(key):
    with pytest.raises(OpsStatusError, match="log/traceback"):
        ops_status.validate_details({key: "x"})


def test_validate_details_allows_key_containing_log_as_substring_of_another_word():
    ops_status.validate_details({"catalog_size": 5})


def test_validate_details_rejects_nested_object():
    with pytest.raises(OpsStatusError, match="unsupported type"):
        ops_status.validate_details({"nested": {"a": 1}})


def test_validate_details_rejects_list_of_objects():
    with pytest.raises(OpsStatusError, match="unsupported type"):
        ops_status.validate_details({"items": [{"a": 1}]})


def test_validate_details_rejects_oversized_list():
    with pytest.raises(OpsStatusError, match="more than"):
        ops_status.validate_details({"items": list(range(ops_status.MAX_DETAIL_LIST_LENGTH + 1))})


def test_validate_details_rejects_oversized_string():
    with pytest.raises(OpsStatusError, match="exceeds"):
        ops_status.validate_details({"note": "x" * (ops_status.MAX_DETAIL_STRING_LENGTH + 1)})


def test_validate_details_allows_null_values():
    ops_status.validate_details({"last_error": None})


def test_validate_details_rejects_key_with_trailing_newline():
    # `_NAME_RE.match` would incorrectly accept this: `$` matches just before a
    # trailing "\n" as well as true end-of-string, and `.match()` never
    # requires consuming the whole string. `fullmatch` closes both gaps.
    with pytest.raises(OpsStatusError, match="lowercase_snake_case"):
        ops_status.validate_details({"abc\n": "value"})


# --- validate_component_status / build_status ---------------------------------


def _status(**overrides):
    fields = dict(
        component="vps_loop",
        state="healthy",
        observed_at=T0,
        last_success_at=T0,
        age_seconds=0,
        details={},
        schema_version=ops_status.SCHEMA_VERSION,
    )
    fields.update(overrides)
    return ops_status.ComponentStatus(**fields)


def test_validate_component_status_accepts_valid_status():
    ops_status.validate_component_status(_status())


def test_validate_component_status_rejects_wrong_schema_version():
    with pytest.raises(OpsStatusError, match="schema_version"):
        ops_status.validate_component_status(_status(schema_version=999))


def test_validate_component_status_rejects_bool_schema_version():
    # `bool` is a subclass of `int`, so `True != SCHEMA_VERSION` is False when
    # `SCHEMA_VERSION == 1`; guard against `bool` explicitly like `age_seconds` does.
    with pytest.raises(OpsStatusError, match="schema_version"):
        ops_status.validate_component_status(_status(schema_version=True))


def test_validate_component_status_rejects_unknown_component():
    with pytest.raises(OpsStatusError, match="unknown component"):
        ops_status.validate_component_status(_status(component="not_a_component"))


def test_validate_component_status_rejects_unknown_state():
    with pytest.raises(OpsStatusError, match="unknown state"):
        ops_status.validate_component_status(_status(state="on_fire"))


def test_validate_component_status_rejects_negative_age():
    with pytest.raises(OpsStatusError, match="age_seconds"):
        ops_status.validate_component_status(_status(age_seconds=-1))


def test_validate_component_status_rejects_naive_observed_at():
    with pytest.raises(OpsStatusError, match="timezone-aware"):
        ops_status.validate_component_status(_status(observed_at=datetime(2026, 9, 11)))


def test_validate_component_status_rejects_age_seconds_without_last_success():
    with pytest.raises(OpsStatusError, match="age_seconds must be null"):
        ops_status.validate_component_status(_status(state="healthy", last_success_at=None, age_seconds=100))


def test_validate_component_status_rejects_state_inconsistent_with_null_last_success():
    with pytest.raises(OpsStatusError, match="inconsistent with last_success_at being null"):
        ops_status.validate_component_status(_status(state="healthy", last_success_at=None, age_seconds=None))


def test_validate_component_status_accepts_unknown_or_failed_with_null_last_success():
    ops_status.validate_component_status(_status(state="unknown", last_success_at=None, age_seconds=None))
    ops_status.validate_component_status(_status(state="failed", last_success_at=None, age_seconds=None))


def test_validate_component_status_rejects_age_seconds_mismatched_with_last_success():
    with pytest.raises(OpsStatusError, match="inconsistent with observed_at/last_success_at"):
        ops_status.validate_component_status(
            _status(state="healthy", observed_at=T0, last_success_at=T0, age_seconds=99999)
        )


def test_validate_component_status_rejects_null_age_when_last_success_present_and_state_not_unknown():
    with pytest.raises(OpsStatusError, match="age_seconds must not be null"):
        ops_status.validate_component_status(
            _status(state="healthy", observed_at=T0, last_success_at=T0, age_seconds=None)
        )


def test_validate_component_status_allows_unknown_with_null_age_on_reversed_timestamps():
    ops_status.validate_component_status(
        _status(state="unknown", observed_at=T0, last_success_at=utc(seconds=1000), age_seconds=None)
    )


def test_validate_component_status_rejects_unknown_state_with_non_null_age_and_last_success():
    # `classify_state` never produces state="unknown" together with a non-null
    # age_seconds when last_success_at is present -- a hand-authored document
    # claiming otherwise must be rejected, not silently accepted.
    with pytest.raises(OpsStatusError, match="state must not be 'unknown'"):
        ops_status.validate_component_status(
            _status(state="unknown", observed_at=T0, last_success_at=T0, age_seconds=0, details={})
        )


def test_validate_component_status_enforces_max_payload_size_even_when_details_pass_individually():
    details = {f"field_{i:02d}": "x" * ops_status.MAX_DETAIL_STRING_LENGTH for i in range(ops_status.MAX_DETAIL_KEYS)}
    ops_status.validate_details(details)  # each field individually passes
    with pytest.raises(OpsStatusError, match=r"-byte limit"):
        ops_status.validate_component_status(_status(details=details))


def test_all_four_named_components_are_accepted():
    for component in ("vps_loop", "github", "oracle_crawler", "r2"):
        ops_status.validate_component_status(_status(component=component))


def test_build_status_derives_state_and_validates():
    status = ops_status.build_status(
        component="r2",
        observed_at=utc(seconds=10),
        last_success_at=T0,
        healthy_max_age_seconds=60,
        stale_max_age_seconds=300,
        now=utc(seconds=10),
        details={"objects": 42},
    )
    assert status.state == "healthy"
    assert status.age_seconds == 10
    assert status.component == "r2"


# --- to_json_dict / from_json_dict round trip ---------------------------------


def test_round_trip_preserves_fields():
    status = _status(details={"agencies": 3})
    document = ops_status.to_json_dict(status)
    restored = ops_status.from_json_dict(document)
    assert restored == status


def test_to_json_dict_uses_z_suffix_for_utc():
    document = ops_status.to_json_dict(_status())
    assert document["observed_at"].endswith("Z")
    assert document["last_success_at"].endswith("Z")


def test_to_json_dict_null_last_success_at():
    document = ops_status.to_json_dict(_status(last_success_at=None, age_seconds=None))
    assert document["last_success_at"] is None
    assert document["age_seconds"] is None


def test_from_json_dict_rejects_bad_timestamp():
    document = ops_status.to_json_dict(_status())
    document["observed_at"] = "not-a-timestamp"
    with pytest.raises(OpsStatusError, match="ISO 8601"):
        ops_status.from_json_dict(document)


def test_from_json_dict_copies_details_instead_of_aliasing():
    # `ComponentStatus` is a frozen, documented immutable snapshot; it must not
    # share a mutable `details` dict with the caller's parsed document.
    document = ops_status.to_json_dict(_status(details={"agencies": 3}))
    status = ops_status.from_json_dict(document)
    document["details"]["agencies"] = 999
    assert status.details["agencies"] == 3


# --- validate_document (raw dict / non-Python producer path) ------------------


def _document(**overrides):
    document = ops_status.to_json_dict(_status())
    document.update(overrides)
    return document


def test_validate_document_accepts_valid_document():
    ops_status.validate_document(_document())


def test_validate_document_rejects_non_object():
    with pytest.raises(OpsStatusError, match="JSON object"):
        ops_status.validate_document([1, 2, 3])


def test_validate_document_rejects_missing_field():
    document = _document()
    del document["age_seconds"]
    with pytest.raises(OpsStatusError, match="missing required field"):
        ops_status.validate_document(document)


def test_validate_document_rejects_unexpected_field():
    document = _document(extra_field="nope")
    with pytest.raises(OpsStatusError, match="unexpected field"):
        ops_status.validate_document(document)


def test_validate_document_rejects_wrong_schema_version():
    with pytest.raises(OpsStatusError, match="schema_version"):
        ops_status.validate_document(_document(schema_version=2))


def test_validate_document_rejects_bool_age_seconds():
    with pytest.raises(OpsStatusError, match="age_seconds"):
        ops_status.validate_document(_document(age_seconds=True))


def test_validate_document_rejects_bool_schema_version():
    # `bool` is a subclass of `int`, so `True != SCHEMA_VERSION` is False when
    # `SCHEMA_VERSION == 1`; guard against `bool` explicitly like `age_seconds` does.
    with pytest.raises(OpsStatusError, match="schema_version"):
        ops_status.validate_document(_document(schema_version=True))


def test_validate_document_rejects_non_string_component_with_ops_status_error():
    # A list is unhashable, so `in COMPONENTS` (a frozenset) would raise a raw
    # `TypeError` instead of the module's own `OpsStatusError` unless the type
    # is checked first -- and `main()` only catches `OpsStatusError`.
    document = _document(component=["vps_loop"])
    with pytest.raises(OpsStatusError, match="component must be a string"):
        ops_status.validate_document(document)


def test_validate_document_rejects_non_string_state_with_ops_status_error():
    document = _document(state=["healthy"])
    with pytest.raises(OpsStatusError, match="state must be a string"):
        ops_status.validate_document(document)


def test_validate_document_rejects_age_seconds_without_last_success():
    document = _document(state="healthy", last_success_at=None, age_seconds=100)
    with pytest.raises(OpsStatusError, match="age_seconds must be null"):
        ops_status.validate_document(document)


def test_validate_document_rejects_state_inconsistent_with_null_last_success():
    document = _document(state="healthy", last_success_at=None, age_seconds=None)
    with pytest.raises(OpsStatusError, match="inconsistent with last_success_at being null"):
        ops_status.validate_document(document)


def test_validate_document_accepts_unknown_or_failed_with_null_last_success():
    ops_status.validate_document(_document(state="unknown", last_success_at=None, age_seconds=None))
    ops_status.validate_document(_document(state="failed", last_success_at=None, age_seconds=None))


def test_validate_document_rejects_age_seconds_mismatched_with_last_success():
    document = _document(state="healthy", age_seconds=99999)
    with pytest.raises(OpsStatusError, match="inconsistent with observed_at/last_success_at"):
        ops_status.validate_document(document)


def test_validate_document_rejects_oversized_payload():
    details = {f"field_{i:02d}": "x" * ops_status.MAX_DETAIL_STRING_LENGTH for i in range(ops_status.MAX_DETAIL_KEYS)}
    document = _document(details=details)
    with pytest.raises(OpsStatusError, match=r"-byte limit"):
        ops_status.validate_document(document)


def test_validate_document_round_trips_through_json_dumps_and_loads():
    document = _document()
    reloaded = json.loads(json.dumps(document))
    ops_status.validate_document(reloaded)


# --- JSON_SCHEMA sanity --------------------------------------------------------


def test_json_schema_lists_all_required_fields():
    assert set(ops_status.JSON_SCHEMA["required"]) == set(ops_status._REQUIRED_FIELDS)


def test_json_schema_component_enum_matches_components_constant():
    assert set(ops_status.JSON_SCHEMA["properties"]["component"]["enum"]) == ops_status.COMPONENTS


def test_json_schema_state_enum_matches_states_constant():
    assert set(ops_status.JSON_SCHEMA["properties"]["state"]["enum"]) == ops_status.STATES


def test_json_schema_encodes_max_payload_bytes():
    assert ops_status.JSON_SCHEMA["maxPayloadBytes"] == ops_status.MAX_PAYLOAD_BYTES


def test_json_schema_details_list_items_enforce_string_length_cap():
    items_schema = ops_status.JSON_SCHEMA["properties"]["details"]["additionalProperties"]["items"]
    assert items_schema["maxLength"] == ops_status.MAX_DETAIL_STRING_LENGTH


def test_json_schema_forbidden_key_pattern_rejects_exact_log_like_keys():
    pattern = re.compile(ops_status.JSON_SCHEMA["properties"]["details"]["propertyNames"]["pattern"])
    for key in sorted(ops_status._FORBIDDEN_KEY_EXACT):
        assert not pattern.match(key), f"schema pattern should reject exact forbidden key {key!r}"


def test_json_schema_forbidden_key_pattern_rejects_credential_like_substrings():
    pattern = re.compile(ops_status.JSON_SCHEMA["properties"]["details"]["propertyNames"]["pattern"])
    for substring in ops_status._FORBIDDEN_KEY_SUBSTRINGS:
        key = f"{substring}_value"
        assert not pattern.match(key), f"schema pattern should reject credential-like key {key!r}"


def test_json_schema_forbidden_key_pattern_allows_ordinary_keys():
    pattern = re.compile(ops_status.JSON_SCHEMA["properties"]["details"]["propertyNames"]["pattern"])
    for key in ("agencies_ok", "note", "ratio", "catalog_size"):
        assert pattern.match(key), f"schema pattern should allow ordinary key {key!r}"


@pytest.mark.parametrize(
    "key",
    ["API_KEY", "Secret", "PASSWORD", "Api_Key", "TOKEN", "AccessKey", "LOG", "Traceback"],
)
def test_json_schema_forbidden_key_pattern_rejects_uppercase_and_mixed_case_keys(key):
    # Reproduces the schema-only-validator gap: a document checked only against
    # `JSON_SCHEMA` (not `validate_details`) must reject these the same way
    # `validate_details` does, which relies on `_NAME_RE` requiring lowercase.
    pattern = re.compile(ops_status.JSON_SCHEMA["properties"]["details"]["propertyNames"]["pattern"])
    assert not pattern.match(key), f"schema pattern should reject non-lowercase forbidden-like key {key!r}"


# --- CLI ------------------------------------------------------------------------


def test_main_validate_exits_zero_for_valid_document(tmp_path, capsys):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    exit_code = ops_status.main(["--validate", str(path)])
    assert exit_code == 0
    assert "OK" in capsys.readouterr().out


def test_main_validate_exits_one_for_invalid_document(tmp_path, capsys):
    path = tmp_path / "status.json"
    document = _document()
    del document["state"]
    path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = ops_status.main(["--validate", str(path)])
    assert exit_code == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_validate_exits_two_for_missing_file(tmp_path, capsys):
    exit_code = ops_status.main(["--validate", str(tmp_path / "missing.json")])
    assert exit_code == 2
    assert "ERROR" in capsys.readouterr().err


def test_main_validate_exits_two_for_invalid_json(tmp_path, capsys):
    path = tmp_path / "status.json"
    path.write_text("{not json", encoding="utf-8")
    exit_code = ops_status.main(["--validate", str(path)])
    assert exit_code == 2
    assert "ERROR" in capsys.readouterr().err
