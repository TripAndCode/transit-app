CREATE TABLE snapshots (
    agency_id    INT  NOT NULL REFERENCES agencies(agency_id),
    report_type  TEXT NOT NULL,
    rendered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    text         TEXT NOT NULL,
    PRIMARY KEY (agency_id, report_type)
);
