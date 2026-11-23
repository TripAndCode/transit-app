"""Record prediction validation reports in prediction_validations.

Each REPORT is the JSON scripts/validate_vp_predictions.py writes for one
agency and range of service dates. Its row keeps the counts the report's
on-time figures were made from (db/migrations/0113_prediction_validations),
so a record states what the validation measured without being read from the
report again. A report is refused, and nothing is written, when it was made
under another SCHEMA than this checkout's, when an archive it needed was
missing, so the range holds days it did not see, or when no stop event had an
observed passage.

The target is the environment's DATABASE_URL; nothing loads .env, so export
it first. The dry run (the default) only reads: it prints each row and whether
it is new or replaces the one recorded for the same agency and dates. --apply
writes them in one transaction.

    poetry run python scripts/record_prediction_validation.py REPORT.json [REPORT.json ...] [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import validate_vp_predictions as vv  # noqa: E402  (sys.path injection above)


class Validation(NamedTuple):
    agency_id: int
    from_date: date
    to_date: date
    report_schema: int
    early_tolerance_sec: int | None
    late_tolerance_sec: int
    stop_events: int
    compared: int
    kept_on_time: int
    observed_on_time: int


_UPSERT = """
INSERT INTO prediction_validations (agency_id, from_date, to_date, report_schema, early_tolerance_sec,
    late_tolerance_sec, stop_events, compared, kept_on_time, observed_on_time)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (agency_id, from_date, to_date) DO UPDATE SET
    report_schema = EXCLUDED.report_schema,
    early_tolerance_sec = EXCLUDED.early_tolerance_sec,
    late_tolerance_sec = EXCLUDED.late_tolerance_sec,
    stop_events = EXCLUDED.stop_events,
    compared = EXCLUDED.compared,
    kept_on_time = EXCLUDED.kept_on_time,
    observed_on_time = EXCLUDED.observed_on_time,
    recorded_at = now()
"""


def validation(summary: dict[str, Any]) -> Validation:
    """The row *summary* records; ValueError when it cannot be recorded."""
    if summary.get("schema") != vv.SCHEMA:
        raise ValueError(f"made under schema {summary.get('schema')}, not {vv.SCHEMA}; run the validation again")
    missing = vv.missing_archives(summary)
    if missing:
        raise ValueError(f"{len(missing)} archive(s) missing, first {missing[0]}; fetch them and run it again")
    on_time = summary["figures"]["on_time"]
    if not on_time["n"]:
        raise ValueError("no stop event had an observed passage")
    # kept_to_observed[kept class][observed class] counts the compared events.
    table = on_time["kept_to_observed"]
    return Validation(
        agency_id=summary["agency_id"],
        from_date=date.fromisoformat(summary["from"]),
        to_date=date.fromisoformat(summary["to"]),
        report_schema=summary["schema"],
        early_tolerance_sec=summary["thresholds"]["on_time_early_tolerance_sec"],
        late_tolerance_sec=summary["thresholds"]["on_time_late_tolerance_sec"],
        stop_events=summary["coverage"]["stop_events"],
        compared=on_time["n"],
        kept_on_time=sum(table["on_time"].values()),
        observed_on_time=sum(row["on_time"] for row in table.values()),
    )


def describe(row: Validation) -> str:
    kept = 100 * row.kept_on_time / row.compared
    observed = 100 * row.observed_on_time / row.compared
    return (
        f"agency {row.agency_id} {row.from_date}..{row.to_date}: {row.compared}/{row.stop_events} stop events "
        f"compared, on time {kept:.1f}% kept vs {observed:.1f}% observed ({kept - observed:+.1f} pt)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", type=Path, metavar="REPORT", help="a validation report's JSON")
    parser.add_argument("--apply", action="store_true", help="write the rows (default: print them)")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("refused: DATABASE_URL is not set", file=sys.stderr)
        return 1
    rows = []
    for path in args.reports:
        try:
            rows.append(validation(json.loads(path.read_text(encoding="utf-8"))))
        except ValueError as why:
            print(f"refused: {path}: {why}", file=sys.stderr)
            return 1
    conn = psycopg2.connect(url)
    try:
        if not args.apply:
            conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SELECT agency_id FROM agencies WHERE agency_id = ANY(%s)", ([row.agency_id for row in rows],))
            agencies = {found for (found,) in cur.fetchall()}
            cur.execute(
                "SELECT agency_id, from_date, to_date FROM prediction_validations WHERE agency_id = ANY(%s)",
                ([row.agency_id for row in rows],),
            )
            recorded = set(cur.fetchall())
        unknown = sorted({row.agency_id for row in rows} - agencies)
        if unknown:
            print(f"refused: no agency {', '.join(map(str, unknown))} in Postgres", file=sys.stderr)
            return 1
        for row in rows:
            replaces = (row.agency_id, row.from_date, row.to_date) in recorded
            print(f"{describe(row)}; {'replaces the recorded one' if replaces else 'new'}")
        if not args.apply:
            print(f"dry run: {len(rows)} row(s) would be written; --apply writes them")
            return 0
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, rows)
        conn.commit()
        print(f"wrote {len(rows)} row(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
