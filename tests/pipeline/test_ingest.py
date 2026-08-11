import io
import tarfile
from unittest.mock import patch

from clickhouse_connect.driver.exceptions import DataError, OperationalError

from pipeline.clickhouse import insert_updates
from pipeline.ingest import ingest, parse_trip_id

_FAKE_ROW = (
    "20260401/ok.pb",
    "2026-04-01T11:37:00",
    "平日_11時37分_系統44372",
    "平日",
    "11:37",
    "44372",
    1,
    120,
)


def _ch_route_codes(ch_client, agency_id):
    result = ch_client.query(
        "SELECT route_code FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY route_code",
        parameters={"agency_id": agency_id},
    )
    return [r[0] for r in result.result_rows]


def test_parse_trip_id_weekday():
    result = parse_trip_id("平日_11時37分_系統44372")
    assert result is not None
    assert result["service"] == "平日"
    assert result["hour"] == "11"
    assert result["minute"] == "37"
    assert result["route"] == "44372"


def test_parse_trip_id_invalid():
    result = parse_trip_id("invalid")
    assert result is None


def test_ingest_creates_rows(pg_conn, ch_client, agency_id, tmp_path):
    """Ingest a fake tarball and verify rows land in ClickHouse's updates
    table with correct agency_id."""
    # 8-tuple shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
    #                 route_code, stop_sequence, dep_delay)
    fake_row = (
        "20260401/TripUpdate_113700.pb",  # file_name
        "2026-04-01T11:37:00",  # captured_at
        "平日_11時37分_系統44372",  # trip_id
        "平日",  # service_type
        "11:37",  # scheduled_time
        "44372",  # route_code
        1,  # stop_sequence
        120,  # dep_delay (seconds)
    )
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row]):
        pb_data = b"\x00"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = len(pb_data)
            tf.addfile(info, io.BytesIO(pb_data))
        tgz_path = tmp_path / "20260401.tar.gz"
        tgz_path.write_bytes(buf.getvalue())

        count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert count == 1
    result = ch_client.query(
        "SELECT route_code, dep_delay, agency_id FROM updates WHERE agency_id = {agency_id:UInt16}",
        parameters={"agency_id": agency_id},
    )
    rows = result.result_rows
    assert len(rows) == 1
    assert rows[0][0] == "44372"
    assert rows[0][1] == 120
    assert rows[0][2] == agency_id


def test_ingest_tarball_member_failure_does_not_wipe_an_earlier_good_members_insert(
    pg_conn, ch_client, agency_id, tmp_path
):
    """One malformed member inside a tarball must only roll back ITS OWN
    insert, not every good member already inserted earlier in the same
    tarball since the last 300-member commit boundary - the identical bug
    class fixed for the loose-.pb loop, reproduced at tarball-member grain."""
    ok_data, bad_data = b"\x00", b"\x01"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("20260401/a_ok.pb", ok_data), ("20260401/z_bad.pb", bad_data)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tgz_path = tmp_path / "20260401.tar.gz"
    tgz_path.write_bytes(buf.getvalue())

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("z_bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert _ch_route_codes(ch_client, agency_id) == ["44372"]  # a_ok.pb's row survives z_bad.pb's failure


def test_ingest_loose_pb_continues_past_one_malformed_file(pg_conn, ch_client, agency_id, tmp_path):
    """One malformed loose .pb file must not abort ingestion of the rest -
    matching the tarball loop right above it in ingest(), which already
    isolates per-tarball failures via try/except+rollback+continue."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    (day_dir / "bad.pb").write_bytes(b"\x00")
    (day_dir / "ok.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert count == 1  # the good file's row still landed
    assert _ch_route_codes(ch_client, agency_id) == ["44372"]


def test_ingest_loose_pb_failure_does_not_wipe_an_earlier_good_files_insert(pg_conn, ch_client, agency_id, tmp_path):
    """A file that fails must only roll back ITS OWN work, not every good
    file already inserted earlier in the same (not-yet-committed) batch.
    a_ok.pb sorts before z_bad.pb, so the good insert happens first and,
    with a bare conn.rollback() on the later failure, would be wiped too -
    isolation must use a per-file SAVEPOINT, not the whole transaction."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    (day_dir / "a_ok.pb").write_bytes(b"\x00")
    (day_dir / "z_bad.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        if file_name.endswith("z_bad.pb"):
            raise ValueError("boom")
        return [_FAKE_ROW]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert _ch_route_codes(ch_client, agency_id) == ["44372"]  # a_ok.pb's row survives z_bad.pb's failure


def test_ingest_tarball_extractfile_failure_does_not_wipe_an_earlier_good_members_insert(
    pg_conn, ch_client, agency_id, tmp_path
):
    """tarfile.extractfile() can raise on a corrupt member (bad header/index)
    even when tarfile.open() and getmembers() succeeded. That must isolate
    like any other per-member failure, not escape the savepoint and roll
    back every good member already inserted earlier in this tarball."""
    ok_data, bad_data = b"\x00", b"\x01"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("20260401/a_ok.pb", ok_data), ("20260401/z_bad.pb", bad_data)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tgz_path = tmp_path / "20260401.tar.gz"
    tgz_path.write_bytes(buf.getvalue())

    real_extractfile = tarfile.TarFile.extractfile

    def flaky_extractfile(self, member):
        if member.name.endswith("z_bad.pb"):
            raise tarfile.ReadError("corrupt member")
        return real_extractfile(self, member)

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[_FAKE_ROW]),
        patch.object(tarfile.TarFile, "extractfile", flaky_extractfile),
    ):
        ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert _ch_route_codes(ch_client, agency_id) == ["44372"]  # a_ok.pb's row survives z_bad.pb's extractfile failure


def test_ingest_dedup_skips_seen_files(pg_conn, ch_client, agency_id, tmp_path):
    """Running ingest twice on the same folder inserts 0 rows the second time."""
    fake_row = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        120,
    )
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row]):
        pb_data = b"\x00"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(pb_data))
        tgz_path = tmp_path / "20260401.tar.gz"
        tgz_path.write_bytes(buf.getvalue())

        first = ingest(str(tmp_path), agency_id, pg_conn, ch_client)
        second = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert first == 1
    assert second == 0


def test_ingest_agency_isolated(pg_conn, ch_client, tmp_path):
    """Two agencies ingesting same file_name don't interfere."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency A", "http://a.example.com/feed.pb"),
        )
        aid_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Agency B", "http://b.example.com/feed.pb"),
        )
        aid_b = cur.fetchone()[0]
    pg_conn.commit()

    fake_row_a = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        60,
    )
    fake_row_b = (
        "20260401/TripUpdate_113700.pb",
        "2026-04-01T11:37:00",
        "平日_11時37分_系統44372",
        "平日",
        "11:37",
        "44372",
        1,
        90,
    )
    pb_data = b"\x00"

    def make_tgz(path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="20260401/TripUpdate_113700.pb")
            info.size = 1
            tf.addfile(info, io.BytesIO(pb_data))
        path.write_bytes(buf.getvalue())

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    make_tgz(dir_a / "20260401.tar.gz")
    make_tgz(dir_b / "20260401.tar.gz")

    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row_a]):
        ingest(str(dir_a), aid_a, pg_conn, ch_client)
    with patch("pipeline.strategies.aomori_regex.parse_feed", return_value=[fake_row_b]):
        ingest(str(dir_b), aid_b, pg_conn, ch_client)

    result_a = ch_client.query(
        "SELECT dep_delay FROM updates WHERE agency_id = {agency_id:UInt16}", parameters={"agency_id": aid_a}
    )
    assert result_a.result_rows[0][0] == 60
    result_b = ch_client.query(
        "SELECT dep_delay FROM updates WHERE agency_id = {agency_id:UInt16}", parameters={"agency_id": aid_b}
    )
    assert result_b.result_rows[0][0] == 90


def test_ingest_batches_clickhouse_inserts_across_files(pg_conn, ch_client, agency_id, tmp_path):
    """Task 8.9: ingest() must not call insert_updates once per source file -
    that fixed ~100ms-per-call overhead is what turned a real agency-1
    backfill into 7h36m wall-clock for 15m of actual CPU work. With 10 loose
    .pb files of 5 rows each (50 rows total, well under _BATCH_ROWS),
    insert_updates should be called once - at ingest()'s trailing flush -
    not 10 times, while every row still lands and the total inserted count
    matches the total row count across all files (no rows lost to batching)."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    n_files = 10
    rows_per_file = 5
    for i in range(n_files):
        (day_dir / f"file_{i:02d}.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        return [
            (file_name, "2026-04-01T11:37:00", f"trip_{file_name}_{k}", "平日", "11:37", "44372", k, 60)
            for k in range(rows_per_file)
        ]

    call_row_counts = []

    def counting_insert(client, aid, rows):
        # _flush() clears its `pending_rows` list right after this call
        # returns, and Mock.call_args stores a reference (not a copy) of
        # that same list object - so snapshotting len(rows) HERE, before the
        # caller can mutate it, is required to see each call's true size.
        call_row_counts.append(len(rows))
        return insert_updates(client, aid, rows)

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed),
        patch("pipeline.ingest.insert_updates", side_effect=counting_insert) as mock_insert,
    ):
        count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert count == n_files * rows_per_file
    assert mock_insert.call_count == 1  # NOT one call per file
    assert sum(call_row_counts) == n_files * rows_per_file  # no rows lost to batching

    result = ch_client.query(
        "SELECT count() FROM updates WHERE agency_id = {agency_id:UInt16}", parameters={"agency_id": agency_id}
    )
    assert result.result_rows[0][0] == n_files * rows_per_file


def test_ingest_does_not_double_process_same_file_key_within_one_run(pg_conn, ch_client, agency_id, tmp_path):
    """A file key that occurs twice within a single ingest() call - once as
    a tarball member, once as a loose .pb sharing the same date+name key -
    must only be ingested once, not twice.

    `done` (the set of already-ingested file keys) is computed once at the
    top of ingest() from ClickHouse and is only updated inside _flush(),
    which runs at most once per _BATCH_ROWS rows (Task 8.9). With only one
    row per file here, no mid-run flush is triggered, so `done` never gets
    updated between the tarball loop and the loose-.pb loop. Before the fix
    (an in-memory `seen` set updated at buffer-time, not flush-time), the
    loose-.pb loop's dedup filter checked the still-stale `done` and did not
    exclude the tarball's already-buffered file, so the shared key's row
    landed twice."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    # Loose .pb sharing the exact same "20260401/dup.pb" key as the tarball
    # member created below.
    (day_dir / "dup.pb").write_bytes(b"\x00")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="20260401/dup.pb")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"\x00"))
    tgz_path = tmp_path / "20260401.tar.gz"
    tgz_path.write_bytes(buf.getvalue())

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        return [(file_name, "2026-04-01T11:37:00", f"trip_{file_name}", "平日", "11:37", "44372", 1, 60)]

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert count == 1  # the shared file key must only be ingested once, not twice
    assert _ch_route_codes(ch_client, agency_id) == ["44372"]


def test_ingest_flush_failure_leaves_all_batch_files_undone_for_retry(pg_conn, ch_client, agency_id, tmp_path):
    """The crash-safety property batching must preserve: a file's rows are
    only ever marked `done` in the same operation that successfully inserted
    them - now at batch granularity. If the flush's insert_updates call
    raises, NONE of the batch's files may be marked done, or their rows
    would be silently lost forever on a crash-and-retry. Simulate one
    failing flush (e.g. a dropped ClickHouse connection): confirm zero rows
    landed and zero were counted; then re-run ingest() (this time
    succeeding) and confirm every file's rows land exactly once - proving
    the whole failed batch was retried, not skipped nor double-inserted."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir()
    n_files = 3
    for i in range(n_files):
        (day_dir / f"file_{i}.pb").write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        return [(file_name, "2026-04-01T11:37:00", f"trip_{file_name}", "平日", "11:37", "44372", 1, 60)]

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed),
        patch("pipeline.ingest.insert_updates", side_effect=RuntimeError("connection dropped")),
    ):
        first_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert first_count == 0
    assert _ch_route_codes(ch_client, agency_id) == []

    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        second_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert second_count == n_files
    assert _ch_route_codes(ch_client, agency_id) == ["44372"] * n_files


def _ch_landed(ch_client, agency_id):
    """Sorted (file_name, stop_sequence) pairs actually persisted - pins the
    exact rows that landed, not just a count, so slice-boundary arithmetic
    (pending_counts) and the zero-row-file branch are both exercised."""
    result = ch_client.query(
        "SELECT file_name, stop_sequence FROM updates WHERE agency_id = {agency_id:UInt16} "
        "ORDER BY file_name, stop_sequence",
        parameters={"agency_id": agency_id},
    )
    return sorted((r[0], r[1]) for r in result.result_rows)


def _make_multi_row_files(tmp_path, agency_id, counts_by_file):
    """Write one empty .pb per key in counts_by_file (values = row count) and
    return a fake_parse_feed that yields that many rows per file, each with a
    distinct stop_sequence (1..n) - so a slice-boundary bug (off-by-one in
    pending_counts) shows up as wrong/missing (file_name, stop_sequence)
    pairs rather than just a wrong total count."""
    day_dir = tmp_path / "20260401"
    day_dir.mkdir(exist_ok=True)
    for name in counts_by_file:
        (day_dir / name).write_bytes(b"\x00")

    def fake_parse_feed(raw, ts, file_name, agency_id, conn):
        pb_name = file_name.split("/")[-1]
        n = counts_by_file[pb_name]
        return [
            (file_name, "2026-04-01T11:37:00", f"trip_{file_name}_{k}", "平日", "11:37", "44372", k, 60)
            for k in range(1, n + 1)
        ]

    return fake_parse_feed


def test_ingest_flush_batch_dataerror_retries_per_file_isolating_the_bad_file(pg_conn, ch_client, agency_id, tmp_path):
    """A DataError from insert_updates() on the WHOLE batch (e.g. one file's
    row has a None in a non-Nullable ClickHouse column, such as route_code
    LowCardinality(String) or stop_sequence UInt16 - clickhouse_connect's
    columnar type check rejects the whole insert call, not just the bad
    row) must not discard every file in the batch - only the actually
    offending file. _flush() must retry file-by-file on a DataError:
    good files' rows land and are marked `done`; the bad file's rows are
    dropped for THIS run (counted as an error, logged by name) but must NOT
    be marked `done`, so the next ingest() run retries just that file -
    not the good files, which must not be re-inserted.

    Files carry DIFFERENT row counts (2 / 0 / 3 / 1), including a zero-row
    file, to pin the pending_counts slice-boundary arithmetic and the
    zero-row-file branch - not just a same-size-files count check."""
    counts = {"a_ok.pb": 2, "m_zero.pb": 0, "q_ok2.pb": 3, "z_bad.pb": 1}
    fake_parse_feed = _make_multi_row_files(tmp_path, agency_id, counts)

    real_insert = insert_updates

    def flaky_insert(client, aid, rows):
        file_names = {r[0] for r in rows}
        if len(file_names) > 1:
            # The whole-batch call: simulates clickhouse_connect rejecting
            # the entire columnar insert because ONE row in it has a None in
            # a non-Nullable column.
            raise DataError("Code: 44. DB::Exception: None in non-Nullable column for the whole batch")
        if any("bad" in fn for fn in file_names):
            # Per-file retry call for the actually-bad file.
            raise DataError("Code: 44. DB::Exception: None in non-Nullable column (route_code)")
        return real_insert(client, aid, rows)

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed),
        patch("pipeline.ingest.insert_updates", side_effect=flaky_insert),
    ):
        first_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    # (a) + (c): the good files' rows (including the zero-row file, trivially)
    # survive the bad file's failure - exact (file_name, stop_sequence) set,
    # not just a count.
    assert first_count == 5  # 2 (a_ok) + 0 (m_zero) + 3 (q_ok2)
    assert _ch_landed(ch_client, agency_id) == [
        ("20260401/a_ok.pb", 1),
        ("20260401/a_ok.pb", 2),
        ("20260401/q_ok2.pb", 1),
        ("20260401/q_ok2.pb", 2),
        ("20260401/q_ok2.pb", 3),
    ]

    # (b): re-running ingest() must retry ONLY the bad file - the good files
    # are already `done` and must not be re-inserted (which would duplicate
    # their rows).
    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        second_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert second_count == 1  # only z_bad.pb's row, retried and now succeeding
    assert _ch_landed(ch_client, agency_id) == [
        ("20260401/a_ok.pb", 1),
        ("20260401/a_ok.pb", 2),
        ("20260401/q_ok2.pb", 1),
        ("20260401/q_ok2.pb", 2),
        ("20260401/q_ok2.pb", 3),
        ("20260401/z_bad.pb", 1),
    ]


def test_ingest_flush_non_dataerror_falls_back_to_whole_batch_discard_no_per_file_retry(
    pg_conn, ch_client, agency_id, tmp_path
):
    """A non-DataError failure (connection drop, timeout, server-side error -
    modeled here with clickhouse_connect's OperationalError) must NOT enter
    the per-file retry path: unlike a DataError, the client can't prove the
    batch never reached the server, so retrying file-by-file could either
    hammer an already-struggling server with hundreds of doomed inserts, or
    duplicate rows that actually committed despite the client raising.
    Confirm the old whole-batch-discard behavior: insert_updates is called
    exactly ONCE (no per-file retry calls at all, even for files that would
    have succeeded), zero rows land, and every file - including the ones
    that were never actually bad - is retried on the next run."""
    counts = {"a_ok.pb": 2, "m_zero.pb": 0, "q_ok2.pb": 3, "z_ok3.pb": 1}
    fake_parse_feed = _make_multi_row_files(tmp_path, agency_id, counts)

    with (
        patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed),
        patch("pipeline.ingest.insert_updates", side_effect=OperationalError("connection dropped")) as mock_insert,
    ):
        first_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert first_count == 0
    assert _ch_landed(ch_client, agency_id) == []
    assert mock_insert.call_count == 1  # the whole batch, NOT retried per file

    # Second run (no failure injected): every file - including the three
    # that would have succeeded under a per-file retry - is retried and
    # lands exactly once, proving none of them were silently duplicated or
    # skipped by the first run's discarded attempt.
    with patch("pipeline.strategies.aomori_regex.parse_feed", side_effect=fake_parse_feed):
        second_count = ingest(str(tmp_path), agency_id, pg_conn, ch_client)

    assert second_count == 6  # 2 + 0 + 3 + 1
    assert _ch_landed(ch_client, agency_id) == [
        ("20260401/a_ok.pb", 1),
        ("20260401/a_ok.pb", 2),
        ("20260401/q_ok2.pb", 1),
        ("20260401/q_ok2.pb", 2),
        ("20260401/q_ok2.pb", 3),
        ("20260401/z_ok3.pb", 1),
    ]
