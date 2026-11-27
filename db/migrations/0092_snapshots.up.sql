-- Frozen snapshots (api/routers/snapshots.py): what one screen rendered for
-- one scope, stored so its link reproduces those figures and their method
-- stamp after any later re-analyze. Rows are written once and never updated;
-- an admin can delete one.
--
-- `snapshot_id` is an unguessable token and the only credential a reader
-- needs. `payloads` holds `report`, the screen's report response per locale,
-- each with its DefinitionMeta; `routes`, the agency's route catalogue the
-- labels are drawn from, which carries no stamp of its own; and `credit`, the
-- open-data credit as plain text per locale. `agency_id` carries no foreign
-- key: a snapshot outlives the agency it describes, so `agency_name` is
-- frozen beside it.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id    TEXT PRIMARY KEY CHECK (snapshot_id ~ '^[A-Za-z0-9_-]{22,64}$'),
    created_by     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    screen         TEXT NOT NULL CHECK (screen IN ('council_report', 'route_table', 'route_page')),
    agency_id      INTEGER NOT NULL,
    agency_name    TEXT NOT NULL,
    params         JSONB NOT NULL,
    payloads       JSONB NOT NULL,
    method_version TEXT NOT NULL,
    title          TEXT CHECK (char_length(title) <= 200),
    note           TEXT CHECK (char_length(note) <= 2000)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON snapshots (created_at DESC);
