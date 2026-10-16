"""`_stream_copy` streams ClickHouse blocks into Postgres COPY, block by block."""

from pipeline import analyze as analyze_mod


class _Stream:
    def __init__(self, blocks):
        self._blocks = blocks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._blocks)


class _Client:
    def __init__(self, blocks):
        self._blocks = blocks
        self.calls = []

    def query_row_block_stream(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return _Stream(self._blocks)


class _Cursor:
    def __init__(self):
        self.copies = []

    def copy_expert(self, sql, buf):
        self.copies.append((sql, buf.read()))


def test_stream_copy_writes_each_block_as_copy_text_and_counts_the_rows():
    analyze_mod._step_ms.clear()
    client = _Client([[(1, None, "a\tb")], [], [(2, "x", "y")]])
    cur = _Cursor()

    copied = analyze_mod._stream_copy(
        cur, client, "SELECT 1", {"p": 1}, "t (a, b, c)", scan_label="s: scan", load_label="s: load"
    )

    assert copied == 2
    assert client.calls == [("SELECT 1", {"p": 1})]
    assert cur.copies == [
        ("COPY t (a, b, c) FROM STDIN", "1\t\\N\ta\\tb\n"),
        ("COPY t (a, b, c) FROM STDIN", "2\tx\ty\n"),
    ]
    assert set(analyze_mod._step_ms) == {"s: scan", "s: load"}
