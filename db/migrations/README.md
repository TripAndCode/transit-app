# db/migrations

Plain-SQL migrations applied by `db/migrate.py` via `gtfs_pipeline.py migrate up|down`
(see `make migrate` / `make migrate-down`). Each migration is a pair of files named
`NNNN_name.up.sql` and `NNNN_name.down.sql`; `NNNN` is a zero-padded, strictly
increasing four-digit version. The runner applies `.up.sql` files in filename order
inside a transaction per migration, tracking applied versions in `schema_migrations`.

## Numbering

A new migration takes the next free number: one higher than the largest `NNNN`
already on disk. Because two branches can claim the same next number
independently, a collision is possible and must be resolved before merge — rename
the later-merged pair to the next actually-free number rather than letting two
migrations share one. Never renumber a migration that has already merged: the
runner and any deployed database key off the number that shipped.

## Up/down pairing

Every `.up.sql` needs a matching `.down.sql` that undoes it, even when the down
side is lossy (see `-- DESTRUCTIVE` below) or a no-op placeholder for something
truly irreversible. `migrate down` looks up a version's down file by glob; a
missing one raises `FileNotFoundError` rather than silently doing nothing.

Prefer replacing an already-merged migration's effect with a new migration over
editing the old file in place. An applied migration is never re-run, so editing
a merged `.up.sql`/`.down.sql` leaves already-migrated databases on the old
definition while fresh ones get the new one — the two silently diverge with no
error to surface it.

## The `-- DESTRUCTIVE` marker

A down migration that cannot be undone without losing data — dropping a column
or table outright, deleting rows with no way to reconstruct them — starts one of
its header comment lines with `-- DESTRUCTIVE`, followed by what is lost. Any
other comment prefix (e.g. one that only mentions the word) does not count; the
line itself must start with the marker after stripping leading whitespace.

`db.migrate.migrate_down` refuses to run a `-- DESTRUCTIVE`-marked down
migration unless called with `force_destructive=True`; the CLI exposes this as
`gtfs_pipeline.py migrate down --force-destructive`. The gate exists so an
accidental or scripted rollback cannot discard data as a side effect — a
destructive rollback has to be asked for explicitly, migration by migration.

## `CONCURRENTLY` is not available here

`CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY` cannot run inside a
transaction block, and the runner wraps every migration's SQL in one. Do not
add a migration that uses `CONCURRENTLY`; it will fail at apply time with a
"cannot run inside a transaction block" error. An index that must be built
without locking out writes on a large table needs a one-off script run outside
`migrate up`, not a migration file.

The migration keeps its plain `CREATE INDEX IF NOT EXISTS`, which builds the
index on a fresh database. On a database that already holds the data, the same
name and definition is built `CONCURRENTLY` before the release is promoted, and
the migration then finds it and skips the build.
`scripts/prebuild_indexes_concurrently.sql` is that script: an index belongs in
it, under a guard on its migration's version, when that migration builds it on
a table that already exists. Its header covers running it and recovering from a
failed build; `tests/unit/test_prebuild_indexes_script.py` holds each statement
to its migration's.

## Compatibility with releases that predate `0053`

`0062_hash_tokens_legacy_compat` keeps a release older than `0053_hash_tokens`
working against the hashed tables: a trigger derives `sid_hash` / `key_hash`
for a row inserted with only the raw `sid` / `key`, and `idx_sessions_sid` /
`idx_api_keys_key` index the raw columns that release looks rows up by. It is
needed for as long as such a release can serve against the database: during
the deploy that applies `0053`, and after any rollback of the app to such a
release.

Drop the two triggers, their functions and the two indexes in a later release,
before or in the same migration as the one that drops the raw `sid` / `key`
columns. The functions read `NEW.sid` / `NEW.key`, so dropping a column while
its trigger exists makes every insert into that table fail. Dropping them also
ends any rollback of the app to a release older than `0053`: it could no longer
sign anyone in.

## Follow-up: dropping the raw `sessions.sid` / `api_keys.key` columns

`0053_hash_tokens` added `sid_hash`/`key_hash` primary keys but left the raw
`sessions.sid` and `api_keys.key` columns in place and nullable, so a writer
that has not yet been updated to only write the hash can keep working during a
rolling deploy. Those raw columns are safe to drop in a follow-up migration
once every row already carries its hash — i.e. no writer is still inserting a
hash-less row. `scripts/check_hash_token_cleanup.py` reports that: it runs a
read-only check against the `DATABASE_URL` it is given and exits non-zero once
the raw columns still exist but no hash-less rows remain, meaning the drop
migration can now be written. Run it via `make check-hash-token-cleanup`.
