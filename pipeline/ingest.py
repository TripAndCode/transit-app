import re
import struct
import tarfile
import pathlib
import urllib.request
from datetime import datetime, timezone
import psycopg2.extras


# ── Protobuf parser (zero external dependencies) ──────────────────────────────

def _read_varint(data, pos):
    result, shift = 0, 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _read_ld(data, pos):
    length, pos = _read_varint(data, pos)
    return data[pos: pos + length], pos + length


def _fields(data):
    pos = 0; f = {}
    while pos < len(data):
        try:
            tw, pos = _read_varint(data, pos)
            fn, wt = tw >> 3, tw & 7
            if wt == 0:
                v, pos = _read_varint(data, pos); f.setdefault(fn, []).append(v)
            elif wt == 2:
                v, pos = _read_ld(data, pos); f.setdefault(fn, []).append(v)
            elif wt == 1:
                v = struct.unpack_from("<Q", data, pos)[0]; pos += 8; f.setdefault(fn, []).append(v)
            elif wt == 5:
                v = struct.unpack_from("<I", data, pos)[0]; pos += 4; f.setdefault(fn, []).append(v)
            else:
                break
        except Exception:
            break
    return f


def _dec(b):
    return b.decode("utf-8") if isinstance(b, bytes) else b


# ── trip_id parser ─────────────────────────────────────────────────────────────

_TRIP_RE_DEFAULT = re.compile(
    r"^(?P<service>.+?)_(?P<hour>\d+)時(?P<minute>\d+)分_系統(?P<route>\d+)$"
)


def parse_trip_id(trip_id: str, pattern: re.Pattern = _TRIP_RE_DEFAULT) -> dict | None:
    m = pattern.match(trip_id)
    if m:
        return m.groupdict()
    return None


def _ts(date_str: str, pb_name: str) -> str:
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


# ── Protobuf row parser ────────────────────────────────────────────────────────

def parse_pb(raw: bytes, captured_at: str, file_name: str, pattern: re.Pattern = _TRIP_RE_DEFAULT) -> list:
    rows = []
    try:
        top = _fields(raw)
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
            stop_id = None
            if 4 in stu:
                stop_id = _dec(stu[4][0])
            arr_delay = arr_time = dep_delay = dep_time = None
            if 2 in stu:
                arr = _fields(stu[2][0])
                arr_delay = arr.get(1, [None])[0]
                arr_time = arr.get(2, [None])[0]
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
                dep_time = dep.get(2, [None])[0]
            rows.append((file_name, captured_at, trip_id, service, sched, route,
                         stop_seq, stop_id, arr_delay, arr_time, dep_delay, dep_time))
    return rows


# ── INSERT SQL (psycopg2: %s placeholders) ────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO updates
      (agency_id, file_name, captured_at, trip_id, service_type, scheduled_time,
       route_code, stop_sequence, dep_delay)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""


def ingest(folder: str, agency_id: int, conn) -> int:
    """Ingest all .pb files from tarballs and loose files in folder.

    Returns the number of new rows inserted.
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

    # load agency-specific trip_id pattern, fall back to Aomori default
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trip_id_pattern FROM agencies WHERE agency_id = %s", (agency_id,)
        )
        row = cur.fetchone()
    if row and row[0]:
        pattern = re.compile(row[0])
    else:
        pattern = _TRIP_RE_DEFAULT

    tarballs = sorted(folder.glob("*.tar.gz")) + sorted(folder.glob("*.tgz"))
    pb_loose = sorted(folder.rglob("*.pb"))
    print(f"Found {len(tarballs)} tar.gz, {len(pb_loose)} loose .pb")

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
                        rows = parse_pb(raw, ts, f"{d}/{pb_name}", pattern=pattern)
                        pg_rows = [
                            (agency_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10])
                            for r in rows
                        ]
                        psycopg2.extras.execute_batch(cur, _INSERT_SQL, pg_rows)
                        n_inserted += len(pg_rows)  # counts attempted rows; ON CONFLICT rows are not subtracted
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
            p for p in pb_loose
            if f"{p.parent.name if re.fullmatch(r'\d{8}', p.parent.name) else ''}/{p.name}" not in done
        ]
        if new_pb:
            print(f"\n{len(new_pb)} loose .pb files")
            for j, path in enumerate(new_pb, 1):
                d = path.parent.name if re.fullmatch(r"\d{8}", path.parent.name) else ""
                ts = _ts(d, path.name)
                rows = parse_pb(path.read_bytes(), ts, f"{d}/{path.name}", pattern=pattern)
                pg_rows = [
                    (agency_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10])
                    for r in rows
                ]
                psycopg2.extras.execute_batch(cur, _INSERT_SQL, pg_rows)
                n_inserted += len(pg_rows)  # counts attempted rows; ON CONFLICT rows are not subtracted
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
    """Fetch the agency's GTFS-RT feed_url and ingest it live.

    Returns the number of new rows inserted.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT feed_url, trip_id_pattern FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()

    if row is None or not row[0]:
        raise ValueError(f"No feed_url configured for agency_id={agency_id!r}")

    feed_url: str = row[0]
    trip_id_pattern_str = row[1] if row[1] else None

    if trip_id_pattern_str:
        pattern = re.compile(trip_id_pattern_str)
    else:
        pattern = _TRIP_RE_DEFAULT

    print(f"Fetching live feed from {feed_url}")
    with urllib.request.urlopen(feed_url, timeout=30) as resp:
        raw = resp.read()

    captured_at = datetime.now(timezone.utc).isoformat()
    file_name = f"live_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    rows = parse_pb(raw, captured_at, file_name, pattern=pattern)
    pg_rows = [
        (agency_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10])
        for r in rows
        if r[3] is not None  # skip unmatched trips
    ]

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, _INSERT_SQL, pg_rows)
    conn.commit()

    n_inserted = len(pg_rows)
    print(f"Done: {n_inserted} rows inserted (live)")
    return n_inserted
