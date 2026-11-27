-- One row per release `gtfs_pipeline.py schema-up` recorded (pipeline/deploys.py
-- says when): the commit its image was built from, NULL when it was built
-- without one, and the migrations it applied.
CREATE TABLE IF NOT EXISTS deploys (
    deploy_id           BIGSERIAL   PRIMARY KEY,
    deployed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    git_sha             TEXT        CHECK (git_sha ~ '^[0-9a-f]{7,40}$'),
    migrations_applied  TEXT[]      NOT NULL DEFAULT '{}'
);
