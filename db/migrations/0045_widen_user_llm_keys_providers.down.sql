-- Rolling back removes gemini/openrouter from the allowed set; a key for a
-- provider the app can no longer call is dead data and would violate the
-- restored CHECK anyway, so discard it rather than block the rollback.
DELETE FROM user_llm_keys WHERE provider IN ('gemini', 'openrouter');

ALTER TABLE user_llm_keys DROP CONSTRAINT user_llm_keys_provider_check;
ALTER TABLE user_llm_keys ADD CONSTRAINT user_llm_keys_provider_check
    CHECK (provider IN ('groq', 'openai', 'cerebras'));
