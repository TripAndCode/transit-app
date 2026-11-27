# Prediction validation

Every delay figure the app reports is a feed's prediction: the latest timely
TripUpdates reading for each stop event (`pipeline.db.build_dedup_ch_sql`),
stamped on every report as the value basis `feed_prediction`
(`pipeline/reports/definition.py: VALUE_BASIS`). A vendor's predictor can sit
early or late of what its buses did, and by a different amount for each feed.
`scripts/validate_vp_predictions.py` measures that gap for one agency over a
range of service dates, from the collector's archives alone.

## What it compares

The collector archives GTFS-RT VehiclePositions for the feeds listed in
`oracle_cloud/v3/etc/vehicle_positions.tsv`
([MIGRATION.md §14](../../oracle_cloud/v3/MIGRATION.md#14-vehiclepositions-archive)),
beside the TripUpdates under `rt/` and the timetables under `static/` in R2.
The script reconstructs each trip's stop passages from its vehicle track and
holds the kept reading of every stop event against the passage observed for
it. Its module docstring is the method: which reading is kept, how a passage
is timed and aligned to the stops, and why a stop event has no passage.

It reads no database and writes none. The report gives:

- the error (kept delay minus observed delay) as MAE, bias and 90th
  percentile, overall and by how long before the passage the kept reading
  came, by hour and by timepoint;
- how many kept stop events had an observed passage, and why the rest did
  not;
- the on-time percentage under the board's definition twice, once from the
  kept delays and once from the observed ones. The difference between the
  two is how far the feed's prediction moves the figure the product reports.

## Running it

From the main checkout, with the transit-archives read credentials exported
as the script's docstring names them (`--fetch` only):

    poetry run python scripts/validate_vp_predictions.py --agency ID \
        --from YYYY-MM-DD --to YYYY-MM-DD --archives DIR --fetch

`--archives` is a local mirror of R2's `rt/`, `vp/` and `static/`; `--fetch`
first downloads what it lacks. The report lands in
`~/.cache/transit-app/vp-validation/<id>/<FROM>_<TO>.json`, with a Markdown
table beside it. R2 keeps `vp/` for `R2_VP_RETENTION_DAYS`
(`oracle_cloud/v3/bin/prune-r2.sh`), so only that far back can be validated.

The measured offsets are a dated finding, not a constant: they are recorded
with their dates (below), never written into the code or this guide. Rerun
the validation when a feed changes vendor or predictor, or when another feed
starts archiving VehiclePositions.

## Recording it

`scripts/record_prediction_validation.py` writes a report's counts to
`prediction_validations`: the service dates, the on-time window, how many
kept stop events there were and how many had an observed passage, and how
many of those were on time by the kept delay and by the observed one. The
counts are kept rather than percentages, so every reader computes the rates
and their difference from one source. A report is refused when it was made
under another `SCHEMA` than the checkout's, when an archive or a timetable
it needed was missing, or when no stop event had an observed passage. Two
reports for the same agency and dates refuse the run, since only one of them
could be recorded.

    poetry run python scripts/record_prediction_validation.py \
        ~/.cache/transit-app/vp-validation/<id>/<FROM>_<TO>.json [...] [--apply]

The target is `DATABASE_URL`, which the script does not load from `.env`.
The dry run prints each row and whether it replaces the one recorded for the
same agency and dates; `--apply` writes them in one transaction.

## What the stamp says

A definition stamp that states its scope's timetables and sources
(`stamp_build`) also gives, for each agency of that scope with a validation
recorded, the newest one: its service dates, its on-time window,
and how many points the on-time rate from the kept delays stood above or
below the one from the observed passages. It is the newest whatever dates
the figures cover, so the stamp always names the dates it was measured on,
and an agency with none recorded gets no line. The stamp's fields are in
[analysis-tab.md](analysis-tab.md).

## What the board does with it

The kept-versus-observed offset differs from feed to feed, and no check on
a feed's own data can see it, so ranking agencies on predicted delays would
order each feed's prediction bias rather than its service. The comparability
gate therefore never ranks a figure whose value basis is in
`PREDICTED_VALUE_BASES` (`pipeline/reports/comparability.py`),
and gives the reason `predicted_basis`
([network-tab.md](network-tab.md#comparability-gate)). The council report
states the same verdict. The route report card grades a route against the
standards whatever the basis, since it orders no agencies.

A value basis built from observed passages is not in that set, so the board
ranks agencies on it as soon as the checks on their data pass. The
VehiclePositions pollers keep running to feed such a basis; their stop
steps in MIGRATION.md §14 apply only once it is abandoned.
