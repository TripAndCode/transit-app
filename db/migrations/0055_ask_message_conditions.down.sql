-- DESTRUCTIVE: drops ask_conversation_messages.conditions.
ALTER TABLE ask_conversation_messages DROP COLUMN IF EXISTS conditions;
