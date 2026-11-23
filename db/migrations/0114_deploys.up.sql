-- One row per release the schema step (`gtfs_pipeline.py schema-up`) brought
-- up: the commit its image was built from, and the migrations it applied.
-- Every deployment runs that step before the new code starts, and a restart
-- runs it again; a run that applies nothing for the same commit as the newest
-- row adds no row (pipeline/deploys.py), so deployed_at is when a release
-- first went live. git_sha is NULL when the image was built without its
-- commit.
CREATE TABLE IF NOT EXISTS deploys (
    deploy_id           BIGSERIAL   PRIMARY KEY,
    deployed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    git_sha             TEXT        CHECK (git_sha ~ '^[0-9a-f]{7,40}$'),
    migrations_applied  TEXT[]      NOT NULL DEFAULT '{}'
);
