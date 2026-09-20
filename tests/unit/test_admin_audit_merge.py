"""Pure logic for the unified `/api/admin/audit` timeline
(`pipeline/query/admin_audit.py`): normalizing `login_events` rows onto the
`admin_audit` shape, merging two already-sorted-desc row lists into one page,
and the opaque cursor's encode/decode round trip.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.query import admin_audit as aa

T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 20, 11, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)


def _audit_row(id_, at, action="user.updated", target_id="1"):
    return aa.normalize_admin_audit(
        {
            "id": id_,
            "at": at,
            "actor_id": 9,
            "action": action,
            "target_type": "user",
            "target_id": target_id,
            "before": '{"role": "user"}',
            "after": '{"role": "admin"}',
            "reason": None,
            "ip": "10.0.0.1",
        }
    )


def _login_row(event_id, at, kind="login", user_id=5, meta=None):
    return {
        "event_id": event_id,
        "at": at,
        "actor_id": user_id,
        "user_id": user_id,
        "kind": kind,
        "meta": meta,
        "ip": "10.0.0.2",
    }


class TestNormalizeAdminAudit:
    def test_parses_jsonb_text_before_after(self):
        row = _audit_row(1, T0)
        assert row["before"] == {"role": "user"}
        assert row["after"] == {"role": "admin"}
        assert row["source"] == "audit"
        assert row["id"] == 1

    def test_passes_through_already_decoded_jsonb(self):
        row = aa.normalize_admin_audit(
            {
                "id": 2,
                "at": T0,
                "actor_id": 9,
                "action": "agency.created",
                "target_type": "agency",
                "target_id": "3",
                "before": None,
                "after": {"agency_name": "Foo"},
                "reason": None,
                "ip": None,
            }
        )
        assert row["before"] is None
        assert row["after"] == {"agency_name": "Foo"}


class TestNormalizeLoginEvent:
    def test_maps_login_kind_to_login_ok(self):
        row = aa.normalize_login_event(_login_row(10, T0, kind="login"))
        assert row["action"] == "login.ok"
        assert row["source"] == "login"
        assert row["id"] == 10
        assert row["target_type"] == "user"
        assert row["target_id"] == "5"
        assert row["before"] is None
        assert row["after"] is None

    def test_maps_login_failed_kind_to_login_fail_with_reason_from_meta(self):
        row = aa.normalize_login_event(
            _login_row(11, T0, kind="login_failed", user_id=None, meta='{"reason": "bad_credentials"}')
        )
        assert row["action"] == "login.fail"
        assert row["target_id"] is None
        assert row["reason"] == "bad_credentials"

    def test_meta_already_a_dict_is_not_re_parsed(self):
        row = aa.normalize_login_event(_login_row(12, T0, kind="login_failed", meta={"reason": "no_email"}))
        assert row["reason"] == "no_email"

    def test_unknown_kind_raises(self):
        with pytest.raises(KeyError):
            aa.normalize_login_event(_login_row(13, T0, kind="role_changed"))


class TestMergeAuditPages:
    def test_merges_two_sources_newest_first(self):
        audit_rows = [_audit_row(2, T0), _audit_row(1, T2)]
        login_rows = [aa.normalize_login_event(_login_row(5, T1))]
        page, next_cursor = aa.merge_audit_pages(audit_rows, login_rows, limit=10)
        assert [r["at"] for r in page] == [T0, T1, T2]
        assert next_cursor is None

    def test_limit_truncates_and_returns_next_cursor(self):
        audit_rows = [_audit_row(3, T0), _audit_row(2, T1), _audit_row(1, T2)]
        page, next_cursor = aa.merge_audit_pages(audit_rows, [], limit=2)
        assert [r["id"] for r in page] == [3, 2]
        assert next_cursor == {"at": T1, "source": "audit", "id": 2}

    def test_exact_fit_has_no_next_cursor(self):
        audit_rows = [_audit_row(2, T0), _audit_row(1, T1)]
        page, next_cursor = aa.merge_audit_pages(audit_rows, [], limit=2)
        assert len(page) == 2
        assert next_cursor is None

    def test_cursor_excludes_the_boundary_row_itself(self):
        # Simulates page 2: caller re-fetched rows with `at <= cursor.at`,
        # which includes the cursor row again -- merge must drop it.
        audit_rows = [_audit_row(2, T1), _audit_row(1, T2)]
        cursor = {"at": T1, "source": "audit", "id": 2}
        page, next_cursor = aa.merge_audit_pages(audit_rows, [], limit=10, cursor=cursor)
        assert [r["id"] for r in page] == [1]
        assert next_cursor is None

    def test_ties_at_same_timestamp_break_deterministically(self):
        a = _audit_row(1, T0)
        b = aa.normalize_login_event(_login_row(1, T0))
        page1, _ = aa.merge_audit_pages([a], [b], limit=10)
        page2, _ = aa.merge_audit_pages([a], [b], limit=10)
        assert [(r["source"], r["id"]) for r in page1] == [(r["source"], r["id"]) for r in page2]


class TestCursorEncodeDecode:
    def test_round_trip(self):
        row = {"at": T0, "source": "audit", "id": 42}
        encoded = aa.encode_cursor(row)
        assert isinstance(encoded, str)
        decoded = aa.decode_cursor(encoded)
        assert decoded == {"at": T0, "source": "audit", "id": 42}

    def test_decode_rejects_garbage(self):
        with pytest.raises(ValueError):
            aa.decode_cursor("not-base64-json!!")

    def test_decode_rejects_wellformed_but_wrong_shape(self):
        import base64
        import json

        bad = base64.urlsafe_b64encode(json.dumps({"foo": "bar"}).encode()).decode()
        with pytest.raises(ValueError):
            aa.decode_cursor(bad)


class TestParseBound:
    def test_date_only_start_is_midnight_utc(self):
        dt = aa.parse_bound("2026-09-20", end=False)
        assert dt == datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)

    def test_date_only_end_is_end_of_day_utc(self):
        dt = aa.parse_bound("2026-09-20", end=True)
        assert dt == datetime(2026, 9, 20, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def test_full_datetime_is_passed_through_with_utc_default(self):
        dt = aa.parse_bound("2026-09-20T15:30:00", end=False)
        assert dt == datetime(2026, 9, 20, 15, 30, 0, tzinfo=timezone.utc)

    def test_none_and_empty_string_are_none(self):
        assert aa.parse_bound(None, end=False) is None
        assert aa.parse_bound("", end=False) is None

    def test_invalid_format_raises_value_error(self):
        with pytest.raises(ValueError):
            aa.parse_bound("not-a-date", end=False)
