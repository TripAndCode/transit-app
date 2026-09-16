DELETE FROM user_llm_keys WHERE provider = 'groq';

ALTER TABLE user_llm_keys DROP CONSTRAINT user_llm_keys_provider_check;
ALTER TABLE user_llm_keys ADD CONSTRAINT user_llm_keys_provider_check
    CHECK (provider IN ('openai', 'gemini'));
