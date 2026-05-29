CREATE TABLE ask_intent_cache (
  signature_hash    CHAR(16)     PRIMARY KEY,
  tool              TEXT         NOT NULL,
  args              JSONB        NOT NULL,
  confidence        REAL         NOT NULL,
  hit_count         INT          NOT NULL DEFAULT 0,
  last_question     TEXT         NOT NULL,
  last_user_action  TEXT,
  promoted_at       TIMESTAMPTZ,
  agency_id         INT          NOT NULL REFERENCES agencies(agency_id),
  created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_used_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_ask_intent_cache_hit_count ON ask_intent_cache (hit_count DESC);
CREATE INDEX ix_ask_intent_cache_last_used ON ask_intent_cache (last_used_at);

ALTER TABLE ask_query_log
  ADD COLUMN signature_hash CHAR(16),
  ADD COLUMN cache_outcome  TEXT;   -- 'hit' | 'miss' | 'bypass'
