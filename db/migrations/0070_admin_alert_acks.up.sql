-- An admin's acknowledgement of a board alert, shared by every admin. The
-- board derives its alerts on each read, so an alert is identified by
-- api.admin_board.alert_key (its level, code, params and link) rather than a
-- row of its own; an alert whose figure changes gets a new key and reads as
-- unacknowledged again. Each acknowledgement lapses after a week, and the
-- acknowledging endpoint drops lapsed rows.
CREATE TABLE IF NOT EXISTS admin_alert_acks (
    alert_key  TEXT        PRIMARY KEY,
    acked_by   INT         REFERENCES users(user_id) ON DELETE SET NULL,
    acked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
