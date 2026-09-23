-- Registry of runtime-overridable kill switches. `pipeline.flags.flag()` is
-- the sole reader: a missing row (or an unreachable database) means "no
-- override", and the caller's own `env_default`/env var wins -- this table
-- augments the existing env-var switches, it does not replace them as the
-- source of truth for a fresh deployment that never touches the admin UI.
--
-- `key` matches an entry in `pipeline.flags.REGISTRY`, not the env var name
-- itself (e.g. `ask_router_enabled`, not `ASK_ROUTER_ENABLED`) -- the two are
-- related by that registry, not by string transformation, so a reader must
-- not derive one from the other.
CREATE TABLE IF NOT EXISTS feature_flags (
    key         TEXT PRIMARY KEY,
    value       BOOLEAN NOT NULL,
    reason      TEXT,
    updated_by  INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
