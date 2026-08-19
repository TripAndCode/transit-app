# Refactor Notes

Things noticed during behavior-preserving refactor slices that look like bugs
or ambiguous behavior, deliberately NOT fixed as part of the simplification
work. Each entry names the slice it came from.

## Slice 2 — `pipeline/query/tools.py` / `tool_queries.py` / `meta_tools.py`

### Ambiguous — needs human decision: two coexisting localization patterns

`pipeline/query/tools.py` centralizes every user-facing string in a
`_LOCALES: dict[tuple[str, str], str]` table keyed on `(template, locale)`,
resolved via `_summary(template, lang, **vars)` with `str.format`
interpolation and a fallback to Japanese when an English entry is missing.

`pipeline/query/meta_tools.py` (same tool-calling family, imported into and
merged with `tools.py`'s `TOOLS`/`_HANDLERS`) instead defines its own
`_summary(text_jp: str, text_en: str, locale: str) -> str` (`meta_tools.py:39`)
that takes the two literal strings inline at each call site — no central
table, no interpolation, no fallback-on-missing-key (there's no key to miss).

Both are reasonable designs on their own, but having two different
localization-string architectures in what is otherwise one cohesive surface
(both modules are merged into the same `TOOLS`/`_HANDLERS` objects at import
time, per `tools.py`'s own comment on that merge) is inconsistent. Whether
`meta_tools.py` should be migrated onto `tools.py`'s `_LOCALES` table (adding
every `describe_data`/`capabilities` string to the shared table) — or the
reverse — is a real design decision affecting every call site in
`meta_tools.py` (~600 lines), not a mechanical dedupe. Not touched here, per
the refactor's "no behavior changes, no unrequested redesigns" constraint.
