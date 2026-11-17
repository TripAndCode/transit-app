# Feed directory

[日本語](README.md)

`japan.csv` lists GTFS-RT feeds with their GTFS static zips, so a new deployment can pick agencies from a list ([deploy guide](../docs/deploy-standalone.en.md)). It is the only copy of this data: `scripts/bus_kyo_association_feeds.py` reads its association rows from it.

Columns: `name_ja`, `prefecture` (the operator's head-office prefecture; the association's list does not state it, so it is filled in by hand and may need correcting), `agency_id` (the id `agencies.csv` uses; blank elsewhere), `realtime_url` (TripUpdates; unique), `static_url`, `ingest_strategy` and `static_strategy` (set only for rows `seed_agencies` can load; blank means "let `add_agency` probe it"), `licence`, `platform`, `source` (where the row came from), `verdict`, `verdict_date`, `verdict_reason`, `note`.

`verdict` is the last `add_agency --dry-run` result (a `pipeline.feed_probe.Verdict` value) with its date and the reason the probe printed. It is a snapshot: a feed can change after its date, and an empty verdict means never probed. A row whose verdict is `accept` joined the timetable at 80% or more on that date.

## Add a feed

1. Take the realtime and static URLs from the publisher, and note where they came from in `source`.
2. Probe it against a throwaway database, never a dev one (the standalone stack's own database is fine):
   `python gtfs_pipeline.py add_agency --dry-run --probe-seconds 60 --feed-url <realtime> --static-url <static>`
   Run it during service hours, since an empty feed gives `empty`.
3. Add the row with the verdict, the date (`YYYY-MM-DD`) and the reason as printed. Quote a refusal's reason as printed too.
4. `pytest tests/unit/test_feeds_catalogue.py` checks the columns and that no feed URL repeats.

## Licence

- Rows from `bus-kyo.or.jp` come from the Hiroshima Bus Association's list (<https://www.bus-kyo.or.jp/gtfs-open-data>), which states that the data is provided under Creative Commons CC0. The page also says the feeds may be interrupted or stopped without notice, and that the operators and system vendors accept no liability for that.
- A blank `licence` means the repository records none. Check the publisher's terms before relying on it.
- Poll gently: one request per feed per minute is the poller's default.
