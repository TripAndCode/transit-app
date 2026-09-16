-- cerebras is dropped from the allowed set below; a key for a provider the
-- app can no longer call is dead data and would violate the new CHECK anyway,
-- so discard it rather than block the migration on existing rows.
DELETE FROM user_llm_keys WHERE provider = 'cerebras';

ALTER TABLE user_llm_keys DROP CONSTRAINT user_llm_keys_provider_check;
ALTER TABLE user_llm_keys ADD CONSTRAINT user_llm_keys_provider_check
    CHECK (provider IN ('groq', 'openai', 'gemini'));
