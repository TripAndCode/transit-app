-- The method stamp (pipeline.reports.definition.DefinitionMeta) an Ask card's
-- figures were made under, as the report computing the same figures states
-- it. NULL is a message with no figures a report computes, and every message
-- saved before this column: no stamp is guessed for those.
ALTER TABLE ask_conversation_messages ADD COLUMN IF NOT EXISTS definition JSONB;
