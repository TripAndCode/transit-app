CREATE TABLE IF NOT EXISTS user_llm_keys (
    user_id       INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    provider      TEXT NOT NULL CHECK (provider IN ('groq', 'openai', 'cerebras')),
    encrypted_key BYTEA NOT NULL,
    key_suffix    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
