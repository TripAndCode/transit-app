-- DESTRUCTIVE: drops ask_conversation_messages.definition, the stamps saved
-- cards were made under, which nothing can rebuild.
ALTER TABLE ask_conversation_messages DROP COLUMN IF EXISTS definition;
