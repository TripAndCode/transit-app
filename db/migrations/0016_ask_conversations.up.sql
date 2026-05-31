CREATE TABLE ask_conversations (
  conversation_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          INT          REFERENCES users(user_id),
  agency_id        INT          NOT NULL REFERENCES agencies(agency_id),
  title            TEXT         NOT NULL,
  filter_ctx       JSONB        NOT NULL DEFAULT '{}',
  pinned           BOOLEAN      NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_ask_conversations_user_updated ON ask_conversations (user_id, updated_at DESC);

CREATE TABLE ask_conversation_messages (
  message_id        BIGSERIAL    PRIMARY KEY,
  conversation_id   UUID         NOT NULL REFERENCES ask_conversations(conversation_id) ON DELETE CASCADE,
  role              TEXT         NOT NULL CHECK (role IN ('user', 'assistant')),
  chip_id           TEXT,
  tool              TEXT,
  args              JSONB,
  signature_hash    CHAR(16),
  result            JSONB,
  rendered_summary  TEXT,
  created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_messages_conversation ON ask_conversation_messages (conversation_id, message_id);
