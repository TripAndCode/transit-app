-- The dow/time_band/service RangeCtx a message's tool dispatch actually ran
-- under. Distinct from `args` (the tool's own arguments, e.g. `route`/`n`)
-- and from the conversation's `filter_ctx` (the *current*, editable state,
-- which can change after this message was dispatched) -- this column is the
-- historical record the Ask evidence card's provenance disclosure reads so
-- a past answer's stated conditions never drift when the conversation's
-- filters are edited later.
ALTER TABLE ask_conversation_messages ADD COLUMN conditions JSONB;
