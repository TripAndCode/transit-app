-- One row per pipeline job: ingest, analyze, weather and static loads, from
-- both entry points (the gtfs_pipeline.py CLI and the cron fallback
-- endpoint). Backs the control board's run timeline.
--
-- The rows this table exists for are the ones nothing else keeps. A completed
-- ingest already leaves its mark in `updates` and `agg_meta`; a job that was
-- DISPLACED -- one that found the ingest/analyze advisory lock held and
-- returned without doing anything (see pipeline/locks.py) -- leaves no trace
-- at all, so a fleet that is quietly losing every other scheduled run looks
-- identical to a healthy one. A `skipped` row here is that evidence.
--
-- Written best-effort by pipeline/runs.py: a failure to record never fails
-- the job, so this table is a faithful record of what was observable, not a
-- guaranteed-complete ledger. Read it as "these runs happened", never as
-- "no other run happened".
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id       BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('ingest', 'analyze', 'weather', 'static')),
    -- NULL for a fleet-wide job (the weather pass covers every configured
    -- station, not one agency) and for a job displaced before it resolved
    -- which agency it was for.
    agency_id    INTEGER REFERENCES agencies(agency_id),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL while `status` is 'running'. A row left 'running' with no
    -- finished_at is a job whose process died before it could close its own
    -- row; the board draws it as still open rather than inventing an end.
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL CHECK (status IN ('running', 'ok', 'skipped', 'error')),
    -- Whatever the job counts as its unit of work (rows ingested, station-days
    -- written). NULL means "this kind reports no count", never zero.
    rows         BIGINT,
    -- How long the advisory-lock acquisition itself took. The lock is
    -- non-blocking, so this is the cost of the attempt, not time spent
    -- queueing behind the holder.
    lock_wait_ms INTEGER,
    error        TEXT,
    -- Set only for an operator-triggered run. ON DELETE SET NULL, matching
    -- login_events: the run happened whether or not the account still exists,
    -- and losing the run would lose operational history to a user deletion.
    requested_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL
);

-- The board reads one day at a time, newest first, and that is the only
-- access pattern: every query here is bounded by started_at.
CREATE INDEX IF NOT EXISTS pipeline_runs_started_at_idx ON pipeline_runs (started_at DESC);
