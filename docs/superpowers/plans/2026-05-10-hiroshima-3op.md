# Hiroshima 3-operator integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 広島電鉄 (8) / 広島バス (9) / 広島交通 (10) GTFS-RT TripUpdate ingestion + static GTFS auto-refresh alongside the existing Aomori (1) pipeline, without breaking Aomori on production.

**Architecture:** Two pluggable strategy registries — one for RT ingest (`aomori_regex` vs `static_join`) and one for static fetch (`aomori_index_scrape` vs `direct_url`). DB-backed agency rows carry the strategy names; `pipeline/ingest.py` becomes a router; new `pipeline/static_fetcher.py` orchestrates daily static refresh. Aomori behaviour is locked in by a regression-golden test before any Hiroshima code lands.

**Tech Stack:** Python 3 + pytest + psycopg2 + PostGIS + protobuf (varint hand-parser, no external dep). Existing migration framework at `db/migrate.py`. Test harness in `tests/conftest.py` auto-redirects to `<dbname>_test` and applies migrations.

---

## Spec reference

`specs/2026-05-10-hiroshima-design.md` (commit `1f3a864`).

## Reality reconciliations vs spec

The spec was written before deep code inspection. These three details are corrected in this plan:

1. **`updates` schema** has only `dep_delay` (not `arr_delay/arr_time/dep_time`). Plan preserves that — Hiroshima rows write only `dep_delay`. The other RT delay/time fields are parsed but not persisted, matching Aomori today.
2. **`updates.service_type`, `scheduled_time`, `route_code` are NOT NULL** in the current schema. `static_join`'s LEFT JOIN can produce NULLs when static is missing a trip_id. Migration `0006` loosens those columns to NULLable.
3. **agencies.csv ≠ runtime registry.** It is the **seed source** for the DB `agencies` table (via `gtfs_pipeline.py seed_agencies`). Strategy info therefore lives both in agencies.csv (for seeding) and as DB columns (for runtime dispatch). Migration `0006` adds the columns; `seed_agencies` is updated to populate them.

## File structure (created/modified)

| File | Status | Responsibility |
|---|---|---|
| `db/migrations/0006_strategy_columns.up.sql` | create | Add `ingest_strategy`, `static_strategy` to `agencies`. Loosen NOT NULL on `updates.service_type/scheduled_time/route_code`. |
| `db/migrations/0006_strategy_columns.down.sql` | create | Reverse |
| `agencies.csv` | modify | Add 2 columns + 3 rows (8/9/10) |
| `gtfs_pipeline.py` | modify | Update `cmd_seed_agencies` to handle new columns. Add `refresh-static` subcommand. |
| `pipeline/strategies/__init__.py` | create | Strategy registry (resolve name → module) |
| `pipeline/strategies/_pb.py` | create | Lifted varint helpers + `_ts()` (path-aware date) + `_dec` |
| `pipeline/strategies/aomori_regex.py` | create | Aomori RT strategy (lifted from current `ingest.py`) |
| `pipeline/strategies/static_join.py` | create | Hiroshima RT strategy (PB-decode + JOIN-INSERT) |
| `pipeline/strategies/aomori_index_scrape.py` | create | Aomori static strategy (HTML scrape ported from `poller_static.sh`) |
| `pipeline/strategies/direct_url.py` | create | Hiroshima static strategy (conditional GET, current+latest, manifest) |
| `pipeline/ingest.py` | modify | Becomes a thin router. `ingest()` and `ingest_live()` delegate to strategies. |
| `pipeline/static_fetcher.py` | create | Orchestrates per-agency static refresh; calls a strategy then `load_static`. |
| `tests/fixtures/aomori_golden.json` | create | Snapshot of Aomori `updates` rows produced by the pre-refactor code |
| `tests/fixtures/aomori_sample.pb` | create | Real captured Aomori pb (1 file is enough) |
| `tests/fixtures/hiroden_tu.bin` | create | Captured 広島電鉄 `trip_updates.bin` |
| `tests/fixtures/hiroden_static.zip` | create | Captured 広島電鉄 `current_data.zip` (subset) |
| `tests/fixtures/hirobus_tu.bin` | create | Captured 広島バス `trip_updates.bin` |
| `tests/fixtures/hirobus_static.zip` | create | Captured 広島バス `current_data.zip` (subset) |
| `tests/fixtures/hirokoh_tu.bin` | create | Captured 広島交通 `trip_updates.bin` |
| `tests/fixtures/hirokoh_static.zip` | create | Captured 広島交通 `current_data.zip` (subset) |
| `tests/test_aomori_regression.py` | create | Aomori byte-identical lock test (Phase 2 gate) |
| `tests/test_static_join.py` | create | Hiroshima static_join unit tests, one fn per op |
| `tests/test_static_fetcher.py` | create | Mocked-HTTP tests for both static strategies |
| `oracle_cloud/poller_v2.sh` | create | New multi-agency RT poller for the Oracle crawler repo (scaffolding) |
| `oracle_cloud/poller_static_v2.sh` | create | New static cron entry point that delegates to `gtfs_pipeline.py refresh-static` |
| `oracle_cloud/CUTOVER.md` | create | Step-by-step cutover runbook for Oracle VM |

---

## Phase 1 — DB migration + agency seeding

### Task 1: Add migration `0006_strategy_columns`

**Files:**
- Create: `db/migrations/0006_strategy_columns.up.sql`
- Create: `db/migrations/0006_strategy_columns.down.sql`
- Test: `tests/test_migrate.py` (extend existing file with one new test)

- [ ] **Step 1.1: Write the failing test for migration version 0006**

Append to `tests/test_migrate.py`:

```python
def test_migration_0006_adds_strategy_columns(pg_conn):
    """0006 adds ingest_strategy + static_strategy on agencies; loosens updates NOT NULL."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'agencies'
              AND column_name IN ('ingest_strategy', 'static_strategy')
            ORDER BY column_name
        """)
        rows = cur.fetchall()
    assert rows == [
        ("ingest_strategy", "YES"),
        ("static_strategy", "YES"),
    ]

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'updates'
              AND column_name IN ('service_type', 'scheduled_time', 'route_code')
            ORDER BY column_name
        """)
        rows = cur.fetchall()
    # all three must be nullable after 0006
    assert all(is_nullable == "YES" for _, is_nullable in rows), rows
```

- [ ] **Step 1.2: Run it to verify failure**

Run: `poetry run pytest tests/test_migrate.py::test_migration_0006_adds_strategy_columns -v`
Expected: FAIL — columns don't exist yet.

- [ ] **Step 1.3: Write the up migration**

Create `db/migrations/0006_strategy_columns.up.sql`:

```sql
ALTER TABLE agencies
    ADD COLUMN IF NOT EXISTS ingest_strategy TEXT,
    ADD COLUMN IF NOT EXISTS static_strategy TEXT;

ALTER TABLE updates
    ALTER COLUMN service_type DROP NOT NULL,
    ALTER COLUMN scheduled_time DROP NOT NULL,
    ALTER COLUMN route_code DROP NOT NULL;
```

- [ ] **Step 1.4: Write the down migration**

Create `db/migrations/0006_strategy_columns.down.sql`:

```sql
-- Restore NOT NULL on updates. Any existing NULL rows must be fixed by hand
-- before rolling back; this is intentionally strict.
ALTER TABLE updates
    ALTER COLUMN service_type SET NOT NULL,
    ALTER COLUMN scheduled_time SET NOT NULL,
    ALTER COLUMN route_code SET NOT NULL;

ALTER TABLE agencies
    DROP COLUMN IF EXISTS static_strategy,
    DROP COLUMN IF EXISTS ingest_strategy;
```

- [ ] **Step 1.5: Verify the test passes**

The `apply_schema` session fixture in `tests/conftest.py` auto-runs `migrate_up` so the new migration applies on test session start. Run:
`poetry run pytest tests/test_migrate.py::test_migration_0006_adds_strategy_columns -v`
Expected: PASS.

- [ ] **Step 1.6: Run full test suite to confirm no regressions**

Run: `poetry run pytest -x`
Expected: All existing tests still pass. (Aomori behaviour is unchanged because no Python code has been touched yet; the loosened NOT NULLs on `updates` cannot break inserts that always populated those fields.)

- [ ] **Step 1.7: Commit**

```bash
git add db/migrations/0006_strategy_columns.up.sql \
        db/migrations/0006_strategy_columns.down.sql \
        tests/test_migrate.py
git commit -m "feat(db): add migration 0006 — agency strategy columns + nullable updates"
```

---

### Task 2: Update `agencies.csv` schema (Aomori only — no Hiroshima rows yet)

**Files:**
- Modify: `agencies.csv`

- [ ] **Step 2.1: Add columns + backfill Aomori**

Replace the file contents (`agencies.csv`) with:

```csv
agency_id,agency_name,feed_url,static_url,ingest_strategy,static_strategy,trip_id_pattern
1,青森市バス,https://aomoricitybus.com/TripUpdate.pb,https://aomoricitybus.com/opendata/index.html,aomori_regex,aomori_index_scrape,
```

Note: `static_url` for Aomori is now the index HTML page (so the `aomori_index_scrape` strategy can find it); previously empty. This does not change current poller behaviour because the Oracle VM still runs the old `poller_static.sh` until Phase 6.

- [ ] **Step 2.2: Commit**

```bash
git add agencies.csv
git commit -m "feat(agencies): add ingest_strategy + static_strategy columns; backfill Aomori"
```

---

### Task 3: Update `seed_agencies` to handle the new columns

**Files:**
- Modify: `gtfs_pipeline.py:48-121` (function `cmd_seed_agencies`)
- Test: extend `tests/test_internal_cron.py` is unrelated; create `tests/test_seed_agencies.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_seed_agencies.py`:

```python
import csv
import os

import pytest

from gtfs_pipeline import cmd_seed_agencies


class _Args:
    def __init__(self, csv_path):
        self.csv = csv_path


def test_seed_agencies_populates_strategy_columns(pg_conn, tmp_path, monkeypatch):
    """Seeding from a CSV with strategy columns must persist them on agencies."""
    csv_path = tmp_path / "agencies.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agency_id", "agency_name", "feed_url", "static_url",
            "ingest_strategy", "static_strategy", "trip_id_pattern",
        ])
        w.writerow([
            "42", "テスト交通", "http://test.example.com/feed.pb",
            "http://test.example.com/static.zip",
            "static_join", "direct_url", "",
        ])

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    cmd_seed_agencies(_Args(str(csv_path)))

    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT agency_id, agency_name, ingest_strategy, static_strategy
            FROM agencies WHERE agency_id = 42
        """)
        row = cur.fetchone()
    assert row == (42, "テスト交通", "static_join", "direct_url")


def test_seed_agencies_blank_strategy_is_null(pg_conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "agencies.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agency_id", "agency_name", "feed_url", "static_url",
            "ingest_strategy", "static_strategy", "trip_id_pattern",
        ])
        w.writerow(["43", "ブランク", "http://blank.example.com/feed.pb", "", "", "", ""])

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    cmd_seed_agencies(_Args(str(csv_path)))

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_strategy, static_strategy FROM agencies WHERE agency_id = 43"
        )
        assert cur.fetchone() == (None, None)
```

- [ ] **Step 3.2: Run the test to verify failure**

Run: `poetry run pytest tests/test_seed_agencies.py -v`
Expected: FAIL — `seed_agencies` ignores the new columns.

- [ ] **Step 3.3: Update `cmd_seed_agencies`**

In `gtfs_pipeline.py`, change `cmd_seed_agencies` to read and persist the two new columns. Replace lines 62-118 (function body up to and including the `setval` block) with:

```python
    import csv

    path = args.csv
    conn = _get_conn()
    inserted = updated = 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                name = row["agency_name"].strip()
                feed = row["feed_url"].strip()
                static = (row.get("static_url") or "").strip() or None
                pattern = (row.get("trip_id_pattern") or "").strip() or None
                ingest_strategy = (row.get("ingest_strategy") or "").strip() or None
                static_strategy = (row.get("static_strategy") or "").strip() or None
                if not name or not feed:
                    continue  # skip blank/comment lines
                aid_raw = (row.get("agency_id") or "").strip()
                explicit_id = int(aid_raw) if aid_raw.isdigit() else None
                if explicit_id is not None:
                    cur.execute(
                        """
                        INSERT INTO agencies (
                            agency_id, agency_name, feed_url, static_url,
                            trip_id_pattern, ingest_strategy, static_strategy
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (feed_url) DO UPDATE SET
                            agency_id = EXCLUDED.agency_id,
                            agency_name = EXCLUDED.agency_name,
                            static_url = EXCLUDED.static_url,
                            trip_id_pattern = EXCLUDED.trip_id_pattern,
                            ingest_strategy = EXCLUDED.ingest_strategy,
                            static_strategy = EXCLUDED.static_strategy
                        RETURNING agency_id, (xmax = 0) AS inserted
                        """,
                        (explicit_id, name, feed, static, pattern, ingest_strategy, static_strategy),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO agencies (
                            agency_name, feed_url, static_url,
                            trip_id_pattern, ingest_strategy, static_strategy
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (feed_url) DO UPDATE SET
                            agency_name = EXCLUDED.agency_name,
                            static_url = EXCLUDED.static_url,
                            trip_id_pattern = EXCLUDED.trip_id_pattern,
                            ingest_strategy = EXCLUDED.ingest_strategy,
                            static_strategy = EXCLUDED.static_strategy
                        RETURNING agency_id, (xmax = 0) AS inserted
                        """,
                        (name, feed, static, pattern, ingest_strategy, static_strategy),
                    )
                aid, was_inserted = cur.fetchone()
                if was_inserted:
                    inserted += 1
                    print(f"  + agency {aid}: {name}")
                else:
                    updated += 1
                    print(f"  ~ agency {aid}: {name} (updated)")
            cur.execute(
                "SELECT setval('agencies_agency_id_seq', "
                "GREATEST((SELECT COALESCE(MAX(agency_id), 0) FROM agencies), 1))"
            )
    conn.commit()
    conn.close()
    print(f"Seeded {inserted} new + {updated} updated from {path}")
```

- [ ] **Step 3.4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_seed_agencies.py -v`
Expected: PASS.

- [ ] **Step 3.5: Run full suite**

Run: `poetry run pytest -x`
Expected: All pass — no other test exercises the new columns yet.

- [ ] **Step 3.6: Commit**

```bash
git add gtfs_pipeline.py tests/test_seed_agencies.py
git commit -m "feat(seed): persist ingest_strategy + static_strategy from agencies.csv"
```

---

## Phase 2 — Aomori regression lock + strategy refactor

### Task 4: Capture Aomori golden snapshot from current code

**Files:**
- Create: `tests/fixtures/aomori_sample.pb` (one real Aomori pb)
- Create: `tests/fixtures/aomori_golden.json` (snapshot of `updates` rows produced by the **current** code)
- Create: `scripts/capture_aomori_golden.py` (one-shot tool, kept for reproducibility)

The golden file is what locks Aomori behaviour. It must be produced by the *pre-refactor* code so the post-refactor diff can be verified byte-identical.

- [ ] **Step 4.1: Capture an Aomori pb fixture**

Run from the repo root (use the existing Oracle SSH key):

```bash
ssh -i oracle_cloud/ssh-key-2026-03-28.key -o StrictHostKeyChecking=no opc@64.110.114.101 \
  "ls /home/opc/app/transportation_analysis/archive/$(date -u +%Y%m%d) | head -1" \
  | xargs -I{} scp -i oracle_cloud/ssh-key-2026-03-28.key \
      opc@64.110.114.101:/home/opc/app/transportation_analysis/archive/$(date -u +%Y%m%d)/{} \
      tests/fixtures/aomori_sample.pb
```

Expected: a file `tests/fixtures/aomori_sample.pb` ~6 KB with `application/octet-stream` content.

If the Oracle VM is unreachable, fall back to:
```bash
curl -sf https://aomoricitybus.com/TripUpdate.pb -o tests/fixtures/aomori_sample.pb
```

- [ ] **Step 4.2: Write `scripts/capture_aomori_golden.py`**

```python
"""One-shot: parse tests/fixtures/aomori_sample.pb with the current pre-refactor
parse_pb and write the resulting rows as JSON to tests/fixtures/aomori_golden.json.

Run once on the pre-refactor commit. The output is checked into git and
becomes the regression target for the post-refactor code.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.ingest import parse_pb, _ts


def main() -> None:
    pb_path = ROOT / "tests" / "fixtures" / "aomori_sample.pb"
    raw = pb_path.read_bytes()

    # Mimic ingest()'s captured_at derivation. Use a fixed date so the snapshot
    # is reproducible regardless of when the script is run.
    captured_at = _ts("20260509", "TripUpdate_120000.pb")
    rows = parse_pb(raw, captured_at, "20260509/TripUpdate_120000.pb")

    # parse_pb returns 12-tuples; serialize to JSON arrays for stability.
    serialized = [list(row) for row in rows]

    out = ROOT / "tests" / "fixtures" / "aomori_golden.json"
    out.write_text(json.dumps(serialized, ensure_ascii=False, indent=2))
    print(f"Wrote {len(serialized)} rows to {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4.3: Run the capture script and inspect the result**

Run: `poetry run python scripts/capture_aomori_golden.py`
Expected output: `Wrote N rows to tests/fixtures/aomori_golden.json` where N > 0.

Inspect with: `head -20 tests/fixtures/aomori_golden.json` — confirm rows have Japanese service names, route codes, etc.

- [ ] **Step 4.4: Commit fixtures**

```bash
git add tests/fixtures/aomori_sample.pb \
        tests/fixtures/aomori_golden.json \
        scripts/capture_aomori_golden.py
git commit -m "test(aomori): capture golden fixture for regression-lock test"
```

---

### Task 5: Write the Aomori regression-lock test (initially passes against current code)

**Files:**
- Create: `tests/test_aomori_regression.py`

- [ ] **Step 5.1: Write the test**

```python
"""Aomori regression-lock test.

Runs parse_pb on the captured fixture and asserts byte-identical output
against tests/fixtures/aomori_golden.json. This test must pass on every
commit from now on; if a refactor changes Aomori output, the test fails
and the refactor is rejected.
"""

import json
import pathlib

from pipeline.ingest import parse_pb, _ts

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"


def test_aomori_parse_pb_matches_golden():
    raw = (FIX_DIR / "aomori_sample.pb").read_bytes()
    captured_at = _ts("20260509", "TripUpdate_120000.pb")

    rows = parse_pb(raw, captured_at, "20260509/TripUpdate_120000.pb")
    actual = [list(r) for r in rows]

    expected = json.loads((FIX_DIR / "aomori_golden.json").read_text())

    assert actual == expected, (
        "Aomori parse_pb output diverged from golden. "
        "If this is intentional, regenerate via scripts/capture_aomori_golden.py."
    )
```

- [ ] **Step 5.2: Run it — must pass**

Run: `poetry run pytest tests/test_aomori_regression.py -v`
Expected: PASS — golden was generated from the same code path the test runs.

- [ ] **Step 5.3: Commit**

```bash
git add tests/test_aomori_regression.py
git commit -m "test(aomori): add regression-lock against golden fixture"
```

---

### Task 6: Create `pipeline/strategies/_pb.py` (lifted helpers)

**Files:**
- Create: `pipeline/strategies/__init__.py`
- Create: `pipeline/strategies/_pb.py`

These are pure utility extractions. No logic change.

- [ ] **Step 6.1: Create the package init**

`pipeline/strategies/__init__.py`:

```python
"""Ingest and static strategies.

Each ingest strategy module exposes:
    parse_feed(pb_bytes: bytes, agency_id: int, conn) -> list[tuple]
        Returns rows ready for the standard updates INSERT (9-tuple, see
        pipeline.strategies._pb.UPDATE_INSERT_SQL).

Each static strategy module exposes:
    fetch(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]
        Returns the path of a freshly persisted GTFS zip ready for load_static,
        or None if no change.

Strategies are resolved by name via STRATEGIES below.
"""

import importlib


def get_ingest_strategy(name: str):
    if not name:
        # back-compat: empty / NULL falls back to Aomori for the single
        # existing production agency.
        name = "aomori_regex"
    return importlib.import_module(f"pipeline.strategies.{name}")


def get_static_strategy(name: str):
    if not name:
        return None  # caller treats "no static strategy" as a skip
    return importlib.import_module(f"pipeline.strategies.{name}")
```

- [ ] **Step 6.2: Create `_pb.py` with the lifted helpers**

`pipeline/strategies/_pb.py`:

```python
"""Shared protobuf + utility helpers for ingest strategies.

Lifted verbatim (with one path-aware change to _ts) from pipeline/ingest.py
so the byte-identical Aomori behaviour is preserved when ingest.py becomes
a router.
"""

import re
import struct
from datetime import datetime


# ── varint protobuf decoder (no external dependencies) ────────────────────────


def _read_varint(data, pos):
    result, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _read_ld(data, pos):
    length, pos = _read_varint(data, pos)
    return data[pos : pos + length], pos + length


def _fields(data):
    pos = 0
    f = {}
    while pos < len(data):
        try:
            tw, pos = _read_varint(data, pos)
            fn, wt = tw >> 3, tw & 7
            if wt == 0:
                v, pos = _read_varint(data, pos)
                f.setdefault(fn, []).append(v)
            elif wt == 2:
                v, pos = _read_ld(data, pos)
                f.setdefault(fn, []).append(v)
            elif wt == 1:
                v = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
                f.setdefault(fn, []).append(v)
            elif wt == 5:
                v = struct.unpack_from("<I", data, pos)[0]
                pos += 4
                f.setdefault(fn, []).append(v)
            else:
                break
        except Exception:
            break
    return f


def _dec(b):
    return b.decode("utf-8") if isinstance(b, bytes) else b


# ── captured_at derivation ────────────────────────────────────────────────────


def _ts(date_str: str, pb_name: str) -> str:
    """Combine archive date dir + pb filename into an ISO timestamp.

    Same semantics as the original pipeline.ingest._ts: looks for
    `_HHMMSS.pb` in the filename and pairs it with date_str (YYYYMMDD).
    Falls back to plain date or 'now' if the format doesn't match.
    """
    m = re.search(r"_(\d{6})\.pb$", pb_name, re.IGNORECASE)
    if m and len(date_str) == 8:
        try:
            return datetime.strptime(date_str + m.group(1), "%Y%m%d%H%M%S").isoformat()
        except Exception:
            pass
    try:
        return datetime.strptime(date_str, "%Y%m%d").isoformat()
    except Exception:
        return datetime.now().isoformat()


# ── INSERT shape shared by all ingest strategies ──────────────────────────────


UPDATE_INSERT_SQL = """
    INSERT INTO updates
      (agency_id, file_name, captured_at, trip_id, service_type, scheduled_time,
       route_code, stop_sequence, dep_delay)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""
```

- [ ] **Step 6.3: Run all tests — nothing should change yet**

Run: `poetry run pytest -x`
Expected: All pass. No code path imports `pipeline.strategies` yet.

- [ ] **Step 6.4: Commit**

```bash
git add pipeline/strategies/__init__.py pipeline/strategies/_pb.py
git commit -m "refactor(pipeline): extract shared pb helpers into strategies/_pb"
```

---

### Task 7: Create `pipeline/strategies/aomori_regex.py`

**Files:**
- Create: `pipeline/strategies/aomori_regex.py`

- [ ] **Step 7.1: Write the strategy**

```python
"""Aomori RT ingest strategy.

Decodes a TripUpdate pb into rows matching pipeline.strategies._pb.UPDATE_INSERT_SQL.
The trip_id regex (provided per agency in DB column trip_id_pattern, defaulting
to the Aomori format) carries route_code, service_type, and scheduled_time.

This strategy expects rows where the regex matches; non-matching trip_ids are
dropped (preserving today's Aomori ingest behaviour).
"""

import re

from pipeline.strategies._pb import _dec, _fields

_TRIP_RE_DEFAULT = re.compile(
    r"^(?P<service>.+?)_(?P<hour>\d+)時(?P<minute>\d+)分_系統(?P<route>\d+)$"
)


def _resolve_pattern(agency_id: int, conn) -> re.Pattern:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trip_id_pattern FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row and row[0]:
        return re.compile(row[0])
    return _TRIP_RE_DEFAULT


def parse_trip_id(trip_id: str, pattern: re.Pattern = _TRIP_RE_DEFAULT) -> dict | None:
    m = pattern.match(trip_id)
    return m.groupdict() if m else None


def parse_feed(
    pb_bytes: bytes,
    captured_at: str,
    file_name: str,
    agency_id: int,
    conn,
) -> list:
    """Return rows shaped for UPDATE_INSERT_SQL.

    Row shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
                route_code, stop_sequence, dep_delay).
    The 9-tuple consumed by INSERT prepends agency_id at insert time.
    """
    pattern = _resolve_pattern(agency_id, conn)
    rows = []
    try:
        top = _fields(pb_bytes)
    except Exception:
        return rows
    for ent_bytes in top.get(2, []):
        ent = _fields(ent_bytes)
        if 3 not in ent:
            continue
        tu = _fields(ent[3][0])
        trip_id = None
        if 1 in tu:
            trip = _fields(tu[1][0])
            if 1 in trip:
                trip_id = _dec(trip[1][0])
        if not trip_id:
            continue
        parsed = parse_trip_id(trip_id, pattern=pattern)
        if parsed is None:
            continue
        service = parsed.get("service")
        hour = parsed.get("hour", "")
        minute = parsed.get("minute", "")
        sched = f"{hour.zfill(2)}:{minute.zfill(2)}" if hour and minute else None
        route = parsed.get("route")
        for stu_bytes in tu.get(2, []):
            stu = _fields(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            dep_delay = None
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
            rows.append(
                (file_name, captured_at, trip_id, service, sched, route, stop_seq, dep_delay)
            )
    return rows
```

- [ ] **Step 7.2: Verify the regression test still passes against the in-tree Aomori code**

The regression test still imports `pipeline.ingest.parse_pb`; that hasn't changed yet. Run:
`poetry run pytest tests/test_aomori_regression.py -v`
Expected: PASS.

- [ ] **Step 7.3: Add a strategy-level parity test**

Append to `tests/test_aomori_regression.py`:

```python
def test_aomori_strategy_matches_golden():
    """The aomori_regex strategy must produce the same effective rows the
    legacy parse_pb does (modulo dropped fields the strategy never emits).
    """
    import psycopg2
    from pipeline.strategies import aomori_regex

    # The strategy needs a DB connection only to look up trip_id_pattern;
    # use the same conftest-managed test DB.
    import os
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) "
                "VALUES (%s, %s) RETURNING agency_id",
                ("青森市バス_test", "http://aomori-test.example.com/feed.pb"),
            )
            aid = cur.fetchone()[0]
        conn.commit()

        raw = (FIX_DIR / "aomori_sample.pb").read_bytes()
        captured_at = _ts("20260509", "TripUpdate_120000.pb")
        rows = aomori_regex.parse_feed(
            raw, captured_at, "20260509/TripUpdate_120000.pb", aid, conn
        )
    finally:
        conn.rollback()
        conn.close()

    expected_full = json.loads((FIX_DIR / "aomori_golden.json").read_text())
    # Project legacy 12-tuple to the 8-tuple the strategy emits
    # (file_name, captured_at, trip_id, service, sched, route, stop_seq, dep_delay)
    expected = [
        [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10]]
        for r in expected_full
    ]
    actual = [list(r) for r in rows]
    assert actual == expected
```

- [ ] **Step 7.4: Run the parity test**

Run: `poetry run pytest tests/test_aomori_regression.py::test_aomori_strategy_matches_golden -v`
Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add pipeline/strategies/aomori_regex.py tests/test_aomori_regression.py
git commit -m "feat(strategies): add aomori_regex strategy + parity test"
```

---

### Task 8: Refactor `pipeline/ingest.py` to a router

**Files:**
- Modify: `pipeline/ingest.py`

This is the only step that changes Aomori's runtime path. The regression test is the safety net.

- [ ] **Step 8.1: Replace `pipeline/ingest.py`**

Full new file content:

```python
"""GTFS-RT ingest router.

Looks up the ingest strategy for an agency and delegates pb decoding to it.
The router owns: file iteration (tarballs + loose .pb), captured_at
derivation, dedup against the updates table, and bulk INSERT.
"""

import pathlib
import re
import tarfile
import urllib.request
from datetime import datetime, timezone

import psycopg2.extras

from pipeline.strategies import get_ingest_strategy
from pipeline.strategies._pb import UPDATE_INSERT_SQL, _ts

# ── Re-exports for back-compat (existing tests import these) ──────────────────
from pipeline.strategies._pb import _dec, _fields, _read_ld, _read_varint  # noqa: F401
from pipeline.strategies.aomori_regex import (  # noqa: F401
    _TRIP_RE_DEFAULT,
    parse_trip_id,
)


def _resolve_strategy_name(agency_id: int, conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_strategy FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return "aomori_regex"  # back-compat for un-migrated agencies


def parse_pb(
    raw: bytes,
    captured_at: str,
    file_name: str,
    pattern: re.Pattern | None = None,
) -> list:
    """Back-compat wrapper used by the regression test and a few unit tests.

    Returns the legacy 12-tuple shape:
      (file_name, captured_at, trip_id, service, sched, route,
       stop_seq, stop_id, arr_delay, arr_time, dep_delay, dep_time)

    Note: the live ingest path no longer calls this function. New code should
    call the strategy module directly.
    """
    from pipeline.strategies._pb import _dec as _d, _fields as _f
    from pipeline.strategies.aomori_regex import _TRIP_RE_DEFAULT, parse_trip_id

    pat = pattern or _TRIP_RE_DEFAULT
    rows = []
    try:
        top = _f(raw)
    except Exception:
        return rows
    for ent_bytes in top.get(2, []):
        ent = _f(ent_bytes)
        if 3 not in ent:
            continue
        tu = _f(ent[3][0])
        trip_id = None
        if 1 in tu:
            trip = _f(tu[1][0])
            if 1 in trip:
                trip_id = _d(trip[1][0])
        if not trip_id:
            continue
        parsed = parse_trip_id(trip_id, pattern=pat)
        if parsed is None:
            continue
        service = parsed.get("service")
        hour = parsed.get("hour", "")
        minute = parsed.get("minute", "")
        sched = f"{hour.zfill(2)}:{minute.zfill(2)}" if hour and minute else None
        route = parsed.get("route")
        for stu_bytes in tu.get(2, []):
            stu = _f(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            stop_id = None
            if 4 in stu:
                stop_id = _d(stu[4][0])
            arr_delay = arr_time = dep_delay = dep_time = None
            if 2 in stu:
                arr = _f(stu[2][0])
                arr_delay = arr.get(1, [None])[0]
                arr_time = arr.get(2, [None])[0]
            if 3 in stu:
                dep = _f(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
                dep_time = dep.get(2, [None])[0]
            rows.append(
                (
                    file_name, captured_at, trip_id, service, sched, route,
                    stop_seq, stop_id, arr_delay, arr_time, dep_delay, dep_time,
                )
            )
    return rows


def ingest(folder: str, agency_id: int, conn) -> int:
    """Ingest all .pb files from tarballs and loose files in folder.

    Dispatches to the agency's ingest strategy. Returns total rows attempted.
    """
    folder = pathlib.Path(folder)
    n_errors = 0
    n_inserted = 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT file_name FROM updates WHERE agency_id = %s",
            (agency_id,),
        )
        done = {r[0] for r in cur.fetchall()}

    strategy_name = _resolve_strategy_name(agency_id, conn)
    strategy = get_ingest_strategy(strategy_name)

    tarballs = sorted(folder.glob("*.tar.gz")) + sorted(folder.glob("*.tgz"))
    pb_loose = sorted(folder.rglob("*.pb"))
    print(f"Found {len(tarballs)} tar.gz, {len(pb_loose)} loose .pb (strategy={strategy_name})")

    with conn.cursor() as cur:
        for i, tgz in enumerate(tarballs, 1):
            date_m = re.search(r"(\d{8})", tgz.stem)
            date_dir = date_m.group(1) if date_m else ""
            print(f"[{i}/{len(tarballs)}] {tgz.name}")
            try:
                with tarfile.open(tgz, "r:gz") as tf:
                    members = []
                    for m in tf.getmembers():
                        if not m.name.endswith(".pb"):
                            continue
                        pb_name = pathlib.Path(m.name).name
                        inner_dir = pathlib.Path(m.name).parent.name
                        d = inner_dir if re.fullmatch(r"\d{8}", inner_dir) else date_dir
                        members.append((m, pb_name, d))
                    new = [(m, pb, d) for m, pb, d in members if f"{d}/{pb}" not in done]
                    print(f"  {len(members)} pb files, {len(new)} new")
                    for j, (member, pb_name, d) in enumerate(new):
                        ts = _ts(d, pb_name)
                        raw = tf.extractfile(member).read()
                        rows = strategy.parse_feed(raw, ts, f"{d}/{pb_name}", agency_id, conn)
                        pg_rows = [(agency_id, *r) for r in rows]
                        psycopg2.extras.execute_batch(cur, UPDATE_INSERT_SQL, pg_rows)
                        n_inserted += len(pg_rows)
                        done.add(f"{d}/{pb_name}")
                        if j % 300 == 0 and j > 0:
                            conn.commit()
                            print(f"    {j}/{len(new)}...")
            except Exception as e:
                print(f"  [ERROR] {e}")
                n_errors += 1
                conn.rollback()
            conn.commit()

        new_pb = [
            p
            for p in pb_loose
            if f"{p.parent.name if re.fullmatch(r'\d{8}', p.parent.name) else ''}/{p.name}"
            not in done
        ]
        if new_pb:
            print(f"\n{len(new_pb)} loose .pb files")
            for j, path in enumerate(new_pb, 1):
                d = path.parent.name if re.fullmatch(r"\d{8}", path.parent.name) else ""
                ts = _ts(d, path.name)
                rows = strategy.parse_feed(path.read_bytes(), ts, f"{d}/{path.name}", agency_id, conn)
                pg_rows = [(agency_id, *r) for r in rows]
                psycopg2.extras.execute_batch(cur, UPDATE_INSERT_SQL, pg_rows)
                n_inserted += len(pg_rows)
                done.add(f"{d}/{path.name}")
                if j % 500 == 0:
                    conn.commit()
                    print(f"  {j}/{len(new_pb)}")
        conn.commit()

    if n_errors:
        print(f"Skipped {n_errors} files with parse errors")
    print(f"\nDone: {n_inserted} new rows inserted")
    return n_inserted


def ingest_live(agency_id: int, conn) -> int:
    """Fetch the agency's GTFS-RT feed_url and ingest it live."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT feed_url FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row is None or not row[0]:
        raise ValueError(f"No feed_url configured for agency_id={agency_id!r}")
    feed_url = row[0]

    strategy_name = _resolve_strategy_name(agency_id, conn)
    strategy = get_ingest_strategy(strategy_name)

    print(f"Fetching live feed from {feed_url} (strategy={strategy_name})")
    with urllib.request.urlopen(feed_url, timeout=30) as resp:
        raw = resp.read()

    captured_at = datetime.now(timezone.utc).isoformat()
    file_name = f"live_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    rows = strategy.parse_feed(raw, captured_at, file_name, agency_id, conn)
    pg_rows = [(agency_id, *r) for r in rows]

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPDATE_INSERT_SQL, pg_rows)
    conn.commit()

    n_inserted = len(pg_rows)
    print(f"Done: {n_inserted} rows inserted (live)")
    return n_inserted
```

- [ ] **Step 8.2: Run the regression test — golden must match**

Run: `poetry run pytest tests/test_aomori_regression.py -v`
Expected: BOTH tests PASS (`test_aomori_parse_pb_matches_golden` via the back-compat shim; `test_aomori_strategy_matches_golden` via the new strategy).

- [ ] **Step 8.3: Run the existing ingest tests**

Run: `poetry run pytest tests/test_ingest.py tests/test_ingest_live.py tests/test_ingest_parse.py -v`
Expected: All PASS. The shim preserves `parse_pb`, `parse_trip_id`, and the underscored helpers.

If `test_ingest.py::test_ingest_creates_rows` fails: it sets up an agency with no `ingest_strategy`. The router's `_resolve_strategy_name` falls back to `aomori_regex`, so it should still work. If it doesn't, debug — do not skip the test.

- [ ] **Step 8.4: Run the full suite**

Run: `poetry run pytest -x`
Expected: All PASS.

- [ ] **Step 8.5: Commit**

```bash
git add pipeline/ingest.py
git commit -m "refactor(ingest): turn ingest.py into a strategy router

Aomori output is locked byte-identical by tests/test_aomori_regression.py."
```

---

## Phase 3 — `static_join` strategy + Hiroden (agency 8)

### Task 9: Capture Hiroden RT + static fixtures

**Files:**
- Create: `tests/fixtures/hiroden_tu.bin`
- Create: `tests/fixtures/hiroden_static.zip`

- [ ] **Step 9.1: Capture the RT pb**

```bash
curl -sf https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin \
  -o tests/fixtures/hiroden_tu.bin
ls -l tests/fixtures/hiroden_tu.bin   # expect 30-100 KB
```

- [ ] **Step 9.2: Capture the static zip**

```bash
curl -sfL https://ajt-mobusta-gtfs.mcapps.jp/static/8/current_data.zip \
  -o tests/fixtures/hiroden_static.zip
ls -l tests/fixtures/hiroden_static.zip   # expect a few MB
```

- [ ] **Step 9.3: Commit fixtures (LFS not needed at these sizes)**

```bash
git add tests/fixtures/hiroden_tu.bin tests/fixtures/hiroden_static.zip
git commit -m "test(fixtures): capture 広島電鉄 RT + static for static_join tests"
```

---

### Task 10: Implement `pipeline/strategies/static_join.py`

**Files:**
- Create: `pipeline/strategies/static_join.py`

- [ ] **Step 10.1: Write the strategy**

```python
"""Hiroshima-style RT ingest strategy.

The trip_id in these feeds is an opaque UUID; route_code, service_type, and
scheduled_time are derived by JOINing to static_trips and static_stop_times
on (agency_id, trip_id, stop_sequence).

Rows where the JOIN misses get NULLs in service_type / scheduled_time;
route_code is taken straight from the RT trip.route_id and is always non-null.
"""

from pipeline.strategies._pb import _dec, _fields


def _decode_rows(pb_bytes: bytes):
    """Yield (trip_id, rt_route_id, stop_sequence, dep_delay) per stop_time_update."""
    try:
        top = _fields(pb_bytes)
    except Exception:
        return
    for ent_bytes in top.get(2, []):
        ent = _fields(ent_bytes)
        if 3 not in ent:
            continue
        tu = _fields(ent[3][0])
        trip_id = rt_route_id = None
        if 1 in tu:
            trip = _fields(tu[1][0])
            if 1 in trip:
                trip_id = _dec(trip[1][0])
            if 5 in trip:
                rt_route_id = _dec(trip[5][0])
        if not trip_id:
            continue
        for stu_bytes in tu.get(2, []):
            stu = _fields(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            dep_delay = None
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
            yield (trip_id, rt_route_id, stop_seq, dep_delay)


def parse_feed(
    pb_bytes: bytes,
    captured_at: str,
    file_name: str,
    agency_id: int,
    conn,
) -> list:
    """Return rows shaped for UPDATE_INSERT_SQL.

    Row shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
                route_code, stop_sequence, dep_delay).
    """
    raw_rows = list(_decode_rows(pb_bytes))
    if not raw_rows:
        return []

    # Resolve service_type + scheduled_time per (trip_id, stop_sequence) via
    # one round-trip to the DB. Doing this server-side as a SELECT (not as
    # part of the eventual INSERT) keeps the result independently testable
    # and avoids construction-order coupling with the router's INSERT.
    keys = list({(r[0], r[2]) for r in raw_rows if r[2] is not None})
    if not keys:
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.trip_id, st.stop_sequence, t.service_id, st.departure_time
            FROM static_stop_times st
            JOIN static_trips t
              ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id
            WHERE st.agency_id = %s
              AND (st.trip_id, st.stop_sequence) = ANY(%s::record[])
            """,
            (agency_id, keys),
        )
        # NOTE: psycopg2's `record[]` casting from a Python list of tuples
        # is brittle across PG versions; if this proves flaky in the
        # smoke test, fall back to a temp-table strategy described in
        # tests/test_static_join.py::test_static_join_keys_fallback.
        joined = {(tid, seq): (svc, dep) for (tid, seq, svc, dep) in cur.fetchall()}

    rows = []
    miss = 0
    for trip_id, rt_route_id, stop_seq, dep_delay in raw_rows:
        svc, sched = joined.get((trip_id, stop_seq), (None, None))
        if svc is None and sched is None:
            miss += 1
        rows.append(
            (
                file_name, captured_at, trip_id,
                svc, sched, rt_route_id, stop_seq, dep_delay,
            )
        )

    if miss:
        print(f"[static_join] agency={agency_id} {miss}/{len(rows)} rows missed JOIN (logged)")
    return rows
```

**Implementation note** on the `record[]` ANY query: PG's record-array cast requires a registered composite type. Some clusters reject the form above. The robust replacement uses a `VALUES` CTE and a JOIN:

```python
        from psycopg2.extras import execute_values

        cur.execute("""
            CREATE TEMP TABLE _sj_keys (trip_id TEXT, stop_sequence INT) ON COMMIT DROP
        """)
        execute_values(cur, "INSERT INTO _sj_keys VALUES %s", keys)
        cur.execute("""
            SELECT t.trip_id, st.stop_sequence, t.service_id, st.departure_time
            FROM _sj_keys k
            JOIN static_stop_times st
              ON st.agency_id = %s
             AND st.trip_id = k.trip_id
             AND st.stop_sequence = k.stop_sequence
            JOIN static_trips t
              ON t.agency_id = st.agency_id
             AND t.trip_id = st.trip_id
        """, (agency_id,))
```

If `test_static_join_basic` (next task) fails on the `record[]` form against your local PG, swap in the temp-table form above and re-run the test. Document the substitution in the commit message.

- [ ] **Step 10.2: Smoke-import the module**

Run: `poetry run python -c "from pipeline.strategies import static_join; print(static_join.parse_feed)"`
Expected: prints a function reference, no errors.

- [ ] **Step 10.3: Commit**

```bash
git add pipeline/strategies/static_join.py
git commit -m "feat(strategies): add static_join (Hiroshima-style PB + DB JOIN)"
```

---

### Task 11: Test `static_join` against the Hiroden fixture

**Files:**
- Create: `tests/test_static_join.py`

- [ ] **Step 11.1: Write the test**

```python
"""static_join unit tests.

Each operator gets one test that loads the captured static zip into the
test DB, runs static_join.parse_feed on the captured RT pb, and asserts
the row shape + JOIN coverage.
"""

import pathlib

import pytest

from pipeline.static_loader import load_static
from pipeline.strategies import static_join

FIX = pathlib.Path(__file__).parent / "fixtures"


def _make_agency(conn, name: str, feed_url: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (name, feed_url),
        )
        aid = cur.fetchone()[0]
    conn.commit()
    return aid


def _run_and_assert(conn, aid: int, pb_path: pathlib.Path):
    raw = pb_path.read_bytes()
    rows = static_join.parse_feed(
        raw, "2026-05-09T12:00:00", "test/sample.bin", aid, conn
    )
    assert rows, "static_join returned zero rows; pb may be empty or malformed"

    with_route = [r for r in rows if r[5] is not None]
    with_svc = [r for r in rows if r[3] is not None]
    with_sched = [r for r in rows if r[4] is not None]

    # route_code is from RT.route_id and must always be present
    assert len(with_route) == len(rows), (
        f"route_code missing on {len(rows) - len(with_route)} rows"
    )
    # JOIN coverage budget: ≥99% of rows have service_type and scheduled_time
    cov_svc = len(with_svc) / len(rows)
    cov_sched = len(with_sched) / len(rows)
    assert cov_svc >= 0.99, f"service_type JOIN coverage {cov_svc:.2%}"
    assert cov_sched >= 0.99, f"scheduled_time JOIN coverage {cov_sched:.2%}"


@pytest.mark.parametrize(
    "feed_url, pb_name, zip_name",
    [
        ("https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin",
         "hiroden_tu.bin", "hiroden_static.zip"),
    ],
)
def test_static_join_hiroden(pg_conn, feed_url, pb_name, zip_name):
    aid = _make_agency(pg_conn, "広島電鉄_test", feed_url)
    load_static(str(FIX / zip_name), aid, pg_conn)
    _run_and_assert(pg_conn, aid, FIX / pb_name)
```

- [ ] **Step 11.2: Run it**

Run: `poetry run pytest tests/test_static_join.py -v`
Expected: PASS. If JOIN coverage is below 99% the captured fixtures may be from disjoint moments — recapture both within a 5-minute window (Step 9.1 then Step 9.2 immediately).

- [ ] **Step 11.3: Commit**

```bash
git add tests/test_static_join.py
git commit -m "test(static_join): assert ≥99% JOIN coverage on Hiroden fixture"
```

---

### Task 12: Add Hiroden (agency 8) to `agencies.csv`

**Files:**
- Modify: `agencies.csv`

- [ ] **Step 12.1: Append the row**

`agencies.csv` becomes:

```csv
agency_id,agency_name,feed_url,static_url,ingest_strategy,static_strategy,trip_id_pattern
1,青森市バス,https://aomoricitybus.com/TripUpdate.pb,https://aomoricitybus.com/opendata/index.html,aomori_regex,aomori_index_scrape,
8,広島電鉄,https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/8/current_data.zip,static_join,direct_url,
```

- [ ] **Step 12.2: Re-seed agencies in the test DB**

Run: `DATABASE_URL=$DATABASE_URL poetry run python gtfs_pipeline.py seed_agencies agencies.csv`
Expected: prints `+ agency 1: 青森市バス` (or `~` if it existed) and `+ agency 8: 広島電鉄`.

- [ ] **Step 12.3: Commit**

```bash
git add agencies.csv
git commit -m "feat(agencies): add agency 8 (広島電鉄)"
```

---

## Phase 4 — `static_fetcher` and the two static strategies

### Task 13: Implement `pipeline/strategies/direct_url.py`

**Files:**
- Create: `pipeline/strategies/direct_url.py`

- [ ] **Step 13.1: Write the strategy**

```python
"""Direct-URL static GTFS fetcher (Hiroshima-style).

For each agency:
  - GET <static_url> (treat as `current_data.zip`)
  - Also try <neighbour>/latest.zip — if its sha256 differs, prefer it (pre-cutover)
  - Persist as <dest_dir>/<agency_id>/gtfs_static_<YYYYMMDD>.zip
  - Update manifest at <dest_dir>/<agency_id>/_manifest.json

Conditional GET via If-Modified-Since / If-None-Match. 304 → no-op.
"""

import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cond_get(url: str, manifest_entry: dict, dest: pathlib.Path) -> tuple[Optional[str], Optional[str]]:
    """GET url with If-Modified-Since/If-None-Match from manifest_entry.

    Returns (last_modified, etag) on 200 (and writes dest), or (None, None) on
    304 / network failure.
    """
    req = urllib.request.Request(url)
    if manifest_entry.get("last_modified"):
        req.add_header("If-Modified-Since", manifest_entry["last_modified"])
    if manifest_entry.get("etag"):
        req.add_header("If-None-Match", manifest_entry["etag"])
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            with dest.open("wb") as f:
                f.write(data)
            return resp.headers.get("Last-Modified"), resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, None
        print(f"[direct_url] HTTP {e.code} for {url}: {e.reason}")
        return None, None
    except urllib.error.URLError as e:
        print(f"[direct_url] network error for {url}: {e}")
        return None, None


def fetch(
    agency_id: int,
    static_url: str,
    dest_dir: pathlib.Path,
) -> Optional[pathlib.Path]:
    """Fetch and persist the freshest GTFS zip for this agency.

    Returns the path of the zip ready for load_static, or None if no change.
    """
    agency_dir = dest_dir / str(agency_id)
    agency_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = agency_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # Derive latest_url from static_url by replacing the basename
    parsed = urllib.parse.urlparse(static_url)
    base = parsed.path.rsplit("/", 1)[0]
    latest_url = parsed._replace(path=f"{base}/latest.zip").geturl()

    tmp_current = agency_dir / "_tmp_current.zip"
    tmp_latest = agency_dir / "_tmp_latest.zip"

    cur_lm, cur_et = _cond_get(static_url, manifest.get("current", {}), tmp_current)
    lat_lm, lat_et = _cond_get(latest_url, manifest.get("latest", {}), tmp_latest)

    cur_sha = _sha256(tmp_current) if tmp_current.exists() else manifest.get("current", {}).get("sha256")
    lat_sha = _sha256(tmp_latest) if tmp_latest.exists() else manifest.get("latest", {}).get("sha256")

    nothing_changed = (
        cur_lm is None and lat_lm is None
        and cur_sha == manifest.get("current", {}).get("sha256")
        and lat_sha == manifest.get("latest", {}).get("sha256")
    )
    if nothing_changed:
        for tmp in (tmp_current, tmp_latest):
            tmp.unlink(missing_ok=True)
        print(f"[direct_url] agency={agency_id} no change")
        return None

    # Pick which to load: prefer latest if it differs from current
    chosen_tmp = tmp_latest if (lat_sha and cur_sha and lat_sha != cur_sha) else tmp_current
    if not chosen_tmp.exists():
        # one variant 304'd, fall back to whichever did download
        chosen_tmp = tmp_current if tmp_current.exists() else tmp_latest

    if not chosen_tmp.exists():
        print(f"[direct_url] agency={agency_id} both variants 304/failed — keeping prior state")
        return None

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    final = agency_dir / f"gtfs_static_{day}.zip"
    chosen_tmp.replace(final)
    # Clean up the other tmp if it still exists
    for tmp in (tmp_current, tmp_latest):
        tmp.unlink(missing_ok=True)

    manifest["current"] = {
        "url": static_url,
        "last_modified": cur_lm or manifest.get("current", {}).get("last_modified"),
        "etag": cur_et or manifest.get("current", {}).get("etag"),
        "sha256": cur_sha,
    }
    manifest["latest"] = {
        "url": latest_url,
        "last_modified": lat_lm or manifest.get("latest", {}).get("last_modified"),
        "etag": lat_et or manifest.get("latest", {}).get("etag"),
        "sha256": lat_sha,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[direct_url] agency={agency_id} persisted {final.name}")
    return final
```

- [ ] **Step 13.2: Smoke import**

Run: `poetry run python -c "from pipeline.strategies import direct_url; print(direct_url.fetch)"`
Expected: prints a function reference.

- [ ] **Step 13.3: Commit**

```bash
git add pipeline/strategies/direct_url.py
git commit -m "feat(strategies): add direct_url static fetcher"
```

---

### Task 14: Implement `pipeline/strategies/aomori_index_scrape.py`

**Files:**
- Create: `pipeline/strategies/aomori_index_scrape.py`

Direct port of `oracle_cloud/poller_static.sh` logic — fetches the index HTML, regex-extracts the gtfs ZIP href, downloads, hashes, persists.

- [ ] **Step 14.1: Write the strategy**

```python
"""Aomori static fetcher (HTML index scrape).

Mirrors the existing oracle_cloud/poller_static.sh: GET the opendata index
page, find the first `gtfs-aomoricitybus*.zip` href, resolve it relative to
the site root, download, sha256, persist as gtfs_static_YYYYMMDD.zip.
"""

import hashlib
import pathlib
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional


_HREF_RE = re.compile(r'href="([^"]*gtfs-aomoricitybus[^"]*\.zip)"')


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve(href: str, index_url: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    parsed = urllib.parse.urlparse(index_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if href.startswith("/"):
        return root + href
    return f"{root}{parsed.path.rsplit('/', 1)[0]}/{href}"


def fetch(
    agency_id: int,
    index_url: str,
    dest_dir: pathlib.Path,
) -> Optional[pathlib.Path]:
    """Fetch and persist the freshest GTFS zip for Aomori.

    Returns the path of the zip ready for load_static, or None on failure.
    Idempotent same-day overwrite (matches existing shell behaviour).
    """
    agency_dir = dest_dir / str(agency_id)
    agency_dir.mkdir(parents=True, exist_ok=True)

    try:
        with urllib.request.urlopen(index_url, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"[aomori_index_scrape] failed to fetch index {index_url}: {e}")
        return None

    m = _HREF_RE.search(html)
    if not m:
        print("[aomori_index_scrape] gtfs-aomoricitybus*.zip href not found")
        return None
    zip_url = _resolve(m.group(1), index_url)

    day = datetime.now().strftime("%Y%m%d")
    final = agency_dir / f"gtfs_static_{day}.zip"
    try:
        with urllib.request.urlopen(zip_url, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        print(f"[aomori_index_scrape] failed to fetch zip {zip_url}: {e}")
        return None

    if data[:2] != b"PK":
        print("[aomori_index_scrape] downloaded file is not a ZIP (missing PK header)")
        return None

    final.write_bytes(data)
    sha = _sha256(final)
    history_path = agency_dir / "fetch_history.csv"
    if not history_path.exists():
        history_path.write_text("timestamp,zip_url,sha256,bytes,file_path\n")
    with history_path.open("a") as f:
        f.write(f"{datetime.now().isoformat()},{zip_url},{sha},{len(data)},{final}\n")

    print(f"[aomori_index_scrape] agency={agency_id} persisted {final.name} (sha256={sha[:12]})")
    return final
```

- [ ] **Step 14.2: Commit**

```bash
git add pipeline/strategies/aomori_index_scrape.py
git commit -m "feat(strategies): add aomori_index_scrape (port of poller_static.sh)"
```

---

### Task 15: Implement `pipeline/static_fetcher.py`

**Files:**
- Create: `pipeline/static_fetcher.py`

- [ ] **Step 15.1: Write the orchestrator**

```python
"""Per-agency static GTFS refresh orchestrator.

Resolves the agency's static_strategy + static_url from the DB, dispatches to
the strategy, and on a fresh zip calls pipeline.static_loader.load_static.
"""

import pathlib
from typing import Optional

from pipeline.static_loader import load_static
from pipeline.strategies import get_static_strategy


def refresh_static(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Refresh static GTFS for one agency. Returns the loaded zip path or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT static_url, static_strategy FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row is None:
        print(f"[static_fetcher] no agency {agency_id}")
        return None
    static_url, strategy_name = row
    if not static_url or not strategy_name:
        print(f"[static_fetcher] agency={agency_id} not configured for static refresh")
        return None

    strategy = get_static_strategy(strategy_name)
    zip_path = strategy.fetch(agency_id, static_url, dest_dir)
    if zip_path is None:
        return None

    load_static(str(zip_path), agency_id, conn)
    print(f"[static_fetcher] agency={agency_id} loaded {zip_path.name}")
    return zip_path


def refresh_all(conn, dest_dir: pathlib.Path) -> int:
    """Refresh static for every agency that has both static_url and static_strategy."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id FROM agencies "
            "WHERE static_url IS NOT NULL AND static_strategy IS NOT NULL "
            "ORDER BY agency_id"
        )
        ids = [r[0] for r in cur.fetchall()]
    n_loaded = 0
    for aid in ids:
        if refresh_static(aid, conn, dest_dir):
            n_loaded += 1
    return n_loaded
```

- [ ] **Step 15.2: Commit**

```bash
git add pipeline/static_fetcher.py
git commit -m "feat(pipeline): add static_fetcher orchestrator"
```

---

### Task 16: Add `refresh-static` CLI subcommand

**Files:**
- Modify: `gtfs_pipeline.py`

- [ ] **Step 16.1: Add the command handler and parser**

In `gtfs_pipeline.py`, after `cmd_load_static` (around line 138), add:

```python
def cmd_refresh_static(args):
    import pathlib
    from pipeline.static_fetcher import refresh_all, refresh_static

    conn = _get_conn()
    dest = pathlib.Path(args.dest)
    if args.agency_id:
        result = refresh_static(int(args.agency_id), conn, dest)
        if result is None:
            print("No change.")
    else:
        n = refresh_all(conn, dest)
        print(f"Refreshed {n} agencies.")
    conn.close()
```

In `main()`, add the subparser (after the `p_static = sub.add_parser("load_static")` block):

```python
    p_refresh = sub.add_parser(
        "refresh-static",
        help="Conditionally fetch + load static GTFS via the agency's static_strategy",
    )
    p_refresh.add_argument("--agency-id", default=None,
                           help="Specific agency (default: all configured)")
    p_refresh.add_argument("--dest", default="raw_archives_static",
                           help="Local destination directory for fetched zips")
```

And add the dispatch in the `if/elif` chain at the bottom of `main()`:

```python
    elif args.command == "refresh-static":
        cmd_refresh_static(args)
```

- [ ] **Step 16.2: Smoke check the CLI**

Run: `poetry run python gtfs_pipeline.py refresh-static --help`
Expected: usage text with `--agency-id` and `--dest`.

- [ ] **Step 16.3: Commit**

```bash
git add gtfs_pipeline.py
git commit -m "feat(cli): add refresh-static subcommand"
```

---

### Task 17: Tests for `static_fetcher` (mocked HTTP)

**Files:**
- Create: `tests/test_static_fetcher.py`

- [ ] **Step 17.1: Write the tests**

```python
"""static_fetcher tests with mocked HTTP."""

import json
import pathlib
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from pipeline.strategies import direct_url, aomori_index_scrape


# ── direct_url ────────────────────────────────────────────────────────────────


def _mock_response(body: bytes, headers=None):
    m = MagicMock()
    m.read.return_value = body
    m.headers = headers or {}
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_direct_url_persists_new_zip(tmp_path):
    body_current = b"PK\x03\x04current"
    body_latest = b"PK\x03\x04current"  # identical → loads current

    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
        _mock_response(body_latest, {"Last-Modified": "lm1", "ETag": "et1"}),
    ]):
        result = direct_url.fetch(
            agency_id=8,
            static_url="https://example.com/static/8/current_data.zip",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == body_current
    manifest = json.loads((tmp_path / "8" / "_manifest.json").read_text())
    assert manifest["current"]["last_modified"] == "lm1"


def test_direct_url_prefers_latest_when_diff(tmp_path):
    body_current = b"PK\x03\x04current_data"
    body_latest = b"PK\x03\x04latest_data"
    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(body_current, {"Last-Modified": "lm1", "ETag": "et1"}),
        _mock_response(body_latest, {"Last-Modified": "lm2", "ETag": "et2"}),
    ]):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is not None
    assert result.read_bytes() == body_latest


def test_direct_url_304_returns_none(tmp_path):
    # Pre-seed manifest so cur_sha == manifest['current']['sha256'] after 304
    agency_dir = tmp_path / "8"
    agency_dir.mkdir(parents=True)
    (agency_dir / "_manifest.json").write_text(json.dumps({
        "current": {"sha256": "x", "last_modified": "lm"},
        "latest": {"sha256": "x", "last_modified": "lm"},
    }))
    err = HTTPError("u", 304, "Not Modified", {}, None)
    with patch("urllib.request.urlopen", side_effect=[err, err]):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is None


def test_direct_url_network_failure_returns_none(tmp_path):
    from urllib.error import URLError
    with patch("urllib.request.urlopen", side_effect=URLError("dns")):
        result = direct_url.fetch(8, "https://example.com/static/8/current_data.zip", tmp_path)
    assert result is None


# ── aomori_index_scrape ───────────────────────────────────────────────────────


def test_aomori_scrape_fetches_resolved_zip(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus-202605.zip">x</a></html>'
    zip_body = b"PK\x03\x04ZIPBODY"

    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(html),
        _mock_response(zip_body),
    ]):
        result = aomori_index_scrape.fetch(
            agency_id=1,
            index_url="https://aomoricitybus.com/opendata/index.html",
            dest_dir=tmp_path,
        )
    assert result is not None
    assert result.read_bytes() == zip_body
    history = (tmp_path / "1" / "fetch_history.csv").read_text()
    assert "gtfs-aomoricitybus-202605.zip" in history


def test_aomori_scrape_no_href_returns_none(tmp_path):
    html = b"<html>no link</html>"
    with patch("urllib.request.urlopen", return_value=_mock_response(html)):
        assert aomori_index_scrape.fetch(1, "https://aomoricitybus.com/opendata/index.html", tmp_path) is None


def test_aomori_scrape_non_zip_body_returns_none(tmp_path):
    html = b'<html><a href="downloads/gtfs-aomoricitybus.zip">x</a></html>'
    not_zip = b"<html>oops</html>"
    with patch("urllib.request.urlopen", side_effect=[
        _mock_response(html),
        _mock_response(not_zip),
    ]):
        assert aomori_index_scrape.fetch(1, "https://aomoricitybus.com/opendata/index.html", tmp_path) is None
```

- [ ] **Step 17.2: Run them**

Run: `poetry run pytest tests/test_static_fetcher.py -v`
Expected: all PASS.

- [ ] **Step 17.3: Commit**

```bash
git add tests/test_static_fetcher.py
git commit -m "test(static): cover direct_url + aomori_index_scrape strategies"
```

---

## Phase 5 — Add 広島バス (9) and 広島交通 (10)

### Task 18: Capture fixtures + add CSV rows for ops 9 and 10

**Files:**
- Create: `tests/fixtures/hirobus_tu.bin`
- Create: `tests/fixtures/hirobus_static.zip`
- Create: `tests/fixtures/hirokoh_tu.bin`
- Create: `tests/fixtures/hirokoh_static.zip`
- Modify: `agencies.csv`
- Modify: `tests/test_static_join.py`

- [ ] **Step 18.1: Capture op 9 fixtures**

```bash
curl -sf https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin \
  -o tests/fixtures/hirobus_tu.bin
curl -sfL https://ajt-mobusta-gtfs.mcapps.jp/static/9/current_data.zip \
  -o tests/fixtures/hirobus_static.zip
```

- [ ] **Step 18.2: Capture op 10 fixtures (same window)**

```bash
curl -sf https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin \
  -o tests/fixtures/hirokoh_tu.bin
curl -sfL https://ajt-mobusta-gtfs.mcapps.jp/static/10/current_data.zip \
  -o tests/fixtures/hirokoh_static.zip
```

- [ ] **Step 18.3: Append rows 9 and 10 to `agencies.csv`**

```csv
agency_id,agency_name,feed_url,static_url,ingest_strategy,static_strategy,trip_id_pattern
1,青森市バス,https://aomoricitybus.com/TripUpdate.pb,https://aomoricitybus.com/opendata/index.html,aomori_regex,aomori_index_scrape,
8,広島電鉄,https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/8/current_data.zip,static_join,direct_url,
9,広島バス,https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/9/current_data.zip,static_join,direct_url,
10,広島交通,https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/10/current_data.zip,static_join,direct_url,
```

- [ ] **Step 18.4: Extend `tests/test_static_join.py` parametrize with ops 9 and 10**

Edit the `@pytest.mark.parametrize` block in `tests/test_static_join.py` to:

```python
@pytest.mark.parametrize(
    "feed_url, pb_name, zip_name, agency_label",
    [
        ("https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin",
         "hiroden_tu.bin", "hiroden_static.zip", "広島電鉄_test"),
        ("https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin",
         "hirobus_tu.bin", "hirobus_static.zip", "広島バス_test"),
        ("https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin",
         "hirokoh_tu.bin", "hirokoh_static.zip", "広島交通_test"),
    ],
)
def test_static_join_per_op(pg_conn, feed_url, pb_name, zip_name, agency_label):
    aid = _make_agency(pg_conn, agency_label, feed_url)
    load_static(str(FIX / zip_name), aid, pg_conn)
    _run_and_assert(pg_conn, aid, FIX / pb_name)
```

(Drop the old single-op `test_static_join_hiroden` function — it is superseded by the parametrized one.)

- [ ] **Step 18.5: Run the parametrized tests**

Run: `poetry run pytest tests/test_static_join.py -v`
Expected: 3 PASS (one per op).

- [ ] **Step 18.6: Commit**

```bash
git add tests/fixtures/hirobus_tu.bin tests/fixtures/hirobus_static.zip \
        tests/fixtures/hirokoh_tu.bin tests/fixtures/hirokoh_static.zip \
        agencies.csv tests/test_static_join.py
git commit -m "feat(agencies): add 広島バス (9) + 広島交通 (10) with parametrized tests"
```

---

## Phase 6 — Oracle VM crawler cutover (separate ops repo / runbook)

The crawler scripts live on the Oracle VM. We provide replacement scripts in this repo under `oracle_cloud/` for hand-deploy. Phase 5 still leaves Aomori production untouched — the cutover is a deliberate ops step.

### Task 19: Author `oracle_cloud/poller_v2.sh` (multi-agency RT poller)

**Files:**
- Create: `oracle_cloud/poller_v2.sh`

- [ ] **Step 19.1: Write the script**

```bash
#!/usr/bin/env bash
# Multi-agency RT poller. Reads /home/opc/app/transportation_analysis/agencies.json
# (exported from agencies.csv) and runs one fetch loop per agency in the background.
#
# agencies.json format: [{"agency_id": 1, "feed_url": "https://..."}, ...]
set -euo pipefail

BASE_DIR="/home/opc/app/transportation_analysis"
ARCHIVE_DIR="$BASE_DIR/archive"
LOG_FILE="$BASE_DIR/poller.log"
AGENCIES_JSON="$BASE_DIR/agencies.json"

INTERVAL=30
MAX_RETRIES=4
RETRY_WAIT=2

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

fetch_pb() {
    local url="$1"
    local dest="$2"
    local attempt=1
    local wait=$RETRY_WAIT
    while [ $attempt -le $MAX_RETRIES ]; do
        if curl -sf --max-time 8 --output "$dest" "$url"; then
            return 0
        fi
        sleep "$wait"
        wait=$(( wait * 2 ))
        attempt=$(( attempt + 1 ))
    done
    return 1
}

agency_loop() {
    local agency_id="$1"
    local feed_url="$2"
    local CURRENT_DAY
    CURRENT_DAY=$(date -u '+%Y%m%d')
    log "[a$agency_id] start (${INTERVAL}s, $feed_url)"
    while true; do
        local NEW_DAY
        NEW_DAY=$(date -u '+%Y%m%d')
        if [ "$NEW_DAY" != "$CURRENT_DAY" ]; then
            local OLD_DIR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY"
            local OLD_TAR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY.tar.gz"
            if [ -d "$OLD_DIR" ]; then
                log "[a$agency_id] tar+rm $CURRENT_DAY"
                nice -n 15 tar -czf "$OLD_TAR" -C "$ARCHIVE_DIR/$agency_id" "$CURRENT_DAY"
                rm -rf "$OLD_DIR"
            fi
            CURRENT_DAY="$NEW_DAY"
        fi
        local DIR="$ARCHIVE_DIR/$agency_id/$CURRENT_DAY"
        local FILE="TripUpdate_$(date -u '+%H%M%S').pb"
        mkdir -p "$DIR"
        if fetch_pb "$feed_url" "$DIR/$FILE"; then
            local SIZE
            SIZE=$(wc -c < "$DIR/$FILE")
            log "[a$agency_id] OK $FILE ($SIZE bytes)"
        else
            log "[a$agency_id] FAIL"
            rm -f "$DIR/$FILE"
        fi
        sleep "$INTERVAL"
    done
}

mkdir -p "$ARCHIVE_DIR"
[ -f "$AGENCIES_JSON" ] || { log "no $AGENCIES_JSON"; exit 1; }

# Spawn one loop per agency
while IFS=$'\t' read -r AID URL; do
    [ -z "$AID" ] && continue
    agency_loop "$AID" "$URL" &
done < <(python3 -c '
import json, sys
data = json.load(open("'"$AGENCIES_JSON"'"))
for a in data:
    if a.get("feed_url"):
        print(f"{a[\"agency_id\"]}\t{a[\"feed_url\"]}")
')

wait
```

- [ ] **Step 19.2: Commit (script only — deploy is a manual ops step)**

```bash
git add oracle_cloud/poller_v2.sh
git commit -m "ops(oracle): add multi-agency poller_v2.sh scaffolding"
```

---

### Task 20: Author `oracle_cloud/poller_static_v2.sh`

**Files:**
- Create: `oracle_cloud/poller_static_v2.sh`

- [ ] **Step 20.1: Write the wrapper**

```bash
#!/usr/bin/env bash
# Daily static GTFS refresh: delegates to gtfs_pipeline.py refresh-static which
# iterates every agency with a configured static_strategy.
set -euo pipefail

BASE_DIR="/home/opc/app/transportation_analysis"
LOG_FILE="$BASE_DIR/static_poller.log"
REPO_DIR="$BASE_DIR/transit-app"   # adjust to actual checkout path

cd "$REPO_DIR"
{
    echo "[$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S %Z')] static refresh start"
    poetry run python gtfs_pipeline.py refresh-static --dest "$BASE_DIR/static_archive"
    echo "[$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S %Z')] static refresh end"
} >> "$LOG_FILE" 2>&1
```

- [ ] **Step 20.2: Commit**

```bash
git add oracle_cloud/poller_static_v2.sh
git commit -m "ops(oracle): add static refresh wrapper that delegates to gtfs_pipeline.py"
```

---

### Task 21: Write the cutover runbook

**Files:**
- Create: `oracle_cloud/CUTOVER.md`

- [ ] **Step 21.1: Write the runbook**

````markdown
# Hiroshima cutover runbook (Oracle VM)

Performs the Phase 6 cutover: replace single-agency pollers with multi-agency
versions, migrate the existing Aomori archive layout under `archive/1/`, and
register agencies 8/9/10. Aomori RT may have a <1-minute gap during the swap;
that's within the existing ingest dedup envelope.

## Pre-cutover checklist (on dev workstation)

- [ ] All Phase 1–5 commits merged on `main`.
- [ ] `pytest -x` green locally.
- [ ] Hiroshima archive disk budget ≥ 50 MB/day projected (Hiroden largest at
  ~7 MB/day at current sizes).
- [ ] You can SSH to `opc@64.110.114.101`.

## Steps

```bash
# 1. SSH in
ssh -i oracle_cloud/ssh-key-2026-03-28.key opc@64.110.114.101

# 2. Pull latest repo on the VM
cd /home/opc/app/transportation_analysis
git -C transit-app pull   # or rsync if no git on the VM

# 3. Apply DB migration 0006
cd transit-app && poetry run python gtfs_pipeline.py migrate up && cd ..

# 4. Re-seed agencies (idempotent upsert; populates the new strategy columns)
cd transit-app && poetry run python gtfs_pipeline.py seed_agencies agencies.csv && cd ..

# 5. Export agencies.json for the v2 poller
cd transit-app && poetry run python -c '
import csv, json, sys
rows = []
for r in csv.DictReader(open("agencies.csv")):
    if r.get("feed_url"):
        rows.append({"agency_id": int(r["agency_id"]), "feed_url": r["feed_url"]})
json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
' > /home/opc/app/transportation_analysis/agencies.json && cd ..

# 6. Migrate existing Aomori archives into per-agency layout
cd /home/opc/app/transportation_analysis
mkdir -p archive/1
shopt -s nullglob
for x in archive/2026[01]*; do mv "$x" archive/1/; done

# 7. Stop the old poller
OLD_PID=$(ps -eo pid,cmd | grep "poller.sh$" | grep -v grep | awk '{print $1}')
[ -n "$OLD_PID" ] && kill "$OLD_PID"

# 8. Replace cron entries
crontab -l > /tmp/old_crontab
cat > /tmp/new_crontab <<'CRON'
@reboot nice -n 10 /home/opc/app/transportation_analysis/poller_v2.sh >> /home/opc/app/transportation_analysis/cron.log 2>&1
CRON_TZ=Asia/Tokyo
0 9 * * * /home/opc/app/transportation_analysis/poller_static_v2.sh
CRON
crontab /tmp/new_crontab

# 9. Install the v2 scripts
cp transit-app/oracle_cloud/poller_v2.sh \
   transit-app/oracle_cloud/poller_static_v2.sh \
   /home/opc/app/transportation_analysis/
chmod +x /home/opc/app/transportation_analysis/poller_*.sh

# 10. Start the v2 poller
nohup nice -n 10 /home/opc/app/transportation_analysis/poller_v2.sh \
  >> /home/opc/app/transportation_analysis/cron.log 2>&1 &
disown

# 11. Verify (within 60s)
ls -la /home/opc/app/transportation_analysis/archive/{1,8,9,10}/$(date -u +%Y%m%d)/ | tail -20
# expect new TripUpdate_*.pb files in all four
```

## Rollback

If something is wrong, restore the prior crontab and the old `poller.sh`:

```bash
crontab /tmp/old_crontab
nohup /home/opc/app/transportation_analysis/poller.sh \
  >> /home/opc/app/transportation_analysis/cron.log 2>&1 &
disown

# Then on the dev workstation:
cd transit-app && poetry run python gtfs_pipeline.py migrate down --target 0005
```

The down migration restores NOT NULL on `updates`. If any Hiroshima rows landed
with NULLs, the down migration will fail; fix by `DELETE FROM updates WHERE
service_type IS NULL OR scheduled_time IS NULL OR route_code IS NULL` first.
````

- [ ] **Step 21.2: Commit**

```bash
git add oracle_cloud/CUTOVER.md
git commit -m "ops(oracle): cutover runbook for Hiroshima rollout"
```

---

## Phase 7 — Production verification

### Task 22: End-to-end smoke per Hiroshima op (post-cutover)

**Files:** none — operational checks only.

- [ ] **Step 22.1: Trigger an `ingest_live` for each Hiroshima agency**

Run on the Oracle VM (or any environment with DATABASE_URL pointing to prod):

```bash
cd /home/opc/app/transportation_analysis/transit-app
for AID in 8 9 10; do
    poetry run python gtfs_pipeline.py ingest_live --agency-id "$AID"
done
```

Expected output per call: `Done: N rows inserted (live)` with N > 0.

- [ ] **Step 22.2: Confirm rows accumulating per agency**

```bash
psql "$DATABASE_URL" -c "
SELECT agency_id, COUNT(*) AS rows, MIN(captured_at) AS first, MAX(captured_at) AS last
FROM updates
WHERE captured_at > now() - interval '1 hour'
GROUP BY agency_id
ORDER BY agency_id
"
```

Expected: 4 rows (agency 1, 8, 9, 10), all with rising counts on consecutive runs.

- [ ] **Step 22.3: Run analyze for each agency**

```bash
for AID in 1 8 9 10; do
    poetry run python gtfs_pipeline.py analyze --agency-id "$AID"
done
```

Expected: per-agency aggregates populated in `agg_route_stats`, `agg_route_hour`, etc.

- [ ] **Step 22.4: Spot-check report shape via API or analyze output**

```bash
psql "$DATABASE_URL" -c "
SELECT agency_id, COUNT(DISTINCT route_code) AS routes, AVG(avg_min)
FROM agg_route_stats GROUP BY agency_id ORDER BY agency_id
"
```

Expected: 4 rows; each has reasonable `routes` count (Aomori dozens, Hiroden ~167, Hirobus ~27, Hirokoh ~86 per the feasibility doc).

- [ ] **Step 22.5: Note completion in the cutover runbook**

Append a "Verified $(date)" line to `oracle_cloud/CUTOVER.md` and commit.

```bash
git add oracle_cloud/CUTOVER.md
git commit -m "ops(oracle): mark Hiroshima cutover verified"
```

---

## Out of scope (deferred)

- VehiclePosition + Alerts ingestion. When prioritised: new `vehicle_positions` table, alerts via `gtfs-realtime-bindings` PyPI dep.
- Combined cross-op rollups (per-op only in v1).
- `calendar.txt` loading for service-active-on-date validation (today's `calendar_dates.txt` coverage suffices).
- Materialized enriched view replacing eager INSERT-time JOIN (graduate when static GTFS revisions need historical updates re-derived).
- Frontend redesign beyond plumbing `agency_id` through any API/route param that doesn't already accept it.

---

## Self-review

### Spec coverage

- ✅ "Two strategies after this change: aomori_regex / static_join" → Tasks 7, 10.
- ✅ "Symmetric pair of static strategies" → Tasks 13, 14.
- ✅ "Crawler stays on Oracle VM. Reads agencies.csv" → Tasks 19–21.
- ✅ "Aomori output byte-identical … locked in by a regression test" → Tasks 4, 5, 8.
- ✅ "Schema unchanged" qualifier → Task 1 documents the necessary nullability change driven by `static_join` LEFT JOIN.
- ✅ Hiroshima archive layout `archive/<agency_id>/<YYYYMMDD>/...` → Task 19 + cutover step 6.
- ✅ Static fetcher CLI `gtfs_pipeline.py refresh-static [--agency-id N]` → Task 16.
- ✅ Per-op tests with ≥99% JOIN coverage → Tasks 11, 18.
- ✅ Mocked-HTTP unit tests for both static strategies → Task 17.
- ✅ End-to-end smoke per op → Task 22.

### Placeholder scan

Searched for "TBD", "TODO", "implement later", "appropriate", "Similar to":
- No "TBD" / "TODO" / "implement later".
- "Similar to" not used.
- One implementation note in Task 10 about a possible `record[]` / temp-table swap with full alternate code shown — counts as "decision documented, not deferred".

### Type / name consistency

- `parse_feed(pb_bytes, captured_at, file_name, agency_id, conn) → list[tuple]` — same signature in `aomori_regex.py` (Task 7) and `static_join.py` (Task 10) and the router (Task 8).
- `fetch(agency_id, static_url, dest_dir) → Optional[pathlib.Path]` — same signature in `direct_url.py` (Task 13) and `aomori_index_scrape.py` (Task 14) and `static_fetcher.py` consumer (Task 15).
- INSERT row shape: 8-tuple from strategies, prepended with agency_id by router → 9-tuple matching `UPDATE_INSERT_SQL`. Consistent.
- Strategy registry resolution: `get_ingest_strategy(name)` and `get_static_strategy(name)` defined in Task 6, called in Tasks 8 and 15. Consistent.
