"""The archive's content key."""

from pipeline.static_archive import content_hash


def test_the_key_is_stable_for_the_same_parts():
    parts = ["static_stop_times:3:42", "static_trips:1:7", "static_routes:1:9"]
    assert content_hash(parts) == content_hash(list(parts))
    assert len(content_hash(parts)) == 32


def test_a_changed_part_changes_the_key():
    assert content_hash(["static_stop_times:3:42"]) != content_hash(["static_stop_times:3:43"])
