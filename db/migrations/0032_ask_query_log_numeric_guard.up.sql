-- 0032_ask_query_log_numeric_guard.up.sql
-- Records whether the post-generation numeric-hallucination guard replaced a
-- free-text LLM answer with its fallback. Nullable: only the Stage-3 LLM path
-- produces a verdict, so rules/embedding rows and pre-guard rows stay NULL,
-- which is distinct from FALSE ("guard ran, answer was clean").

ALTER TABLE ask_query_log
  ADD COLUMN IF NOT EXISTS numeric_guard_triggered BOOLEAN;
