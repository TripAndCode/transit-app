# Ask tab

Chat-first, deterministic-by-default Q&A over an agency's delay data. See
`README.md` ▸ "Ask tab — how it works" for the architecture summary this
doc expands on with file-level detail.

## How a user reaches it

- Route: `/agencies/:agencyId/ask`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded). It is **not** the default landing tab — a bare
  `agencies/:agencyId` navigates to `overview`
  (`frontend/src/main.tsx`), and `frontend/src/components/OnboardingGate.tsx`
  redirects a fresh/remembered agency selection to `/agencies/{id}/map`.
  Reach the Ask tab by clicking "Ask" in the sidebar.
- Sidebar nav link: `frontend/src/components/Sidebar.tsx` (`nav.ask` i18n
  key).
- Top-level component: `frontend/src/tabs/AskTab.tsx` — owns thread
  selection, the shared filter context (date range / DOW / time-band /
  service / routes), message dispatch, and anon-to-authenticated
  conversation migration on first login.

What the user sees/does:

- **Empty thread (landing state)** —
  `frontend/src/tabs/ask/AskLandingCards.tsx`: `buildCardTemplates()`
  (`frontend/src/components/askCardTemplates.ts`) defines 5 templates
  total, split by `needsRoute()` into 2 instant-run cards that need no
  route ("🏆 Top-N delays" / `top_delay`, "🎯 On-time rate" /
  `ontime_rank`) which dispatch immediately on click, and 3 route-requiring
  "pills" ("📈 Route delay trend" / `route_trend`, "⚖️ Weekday vs Weekend" /
  `weekday_vs_weekend`, "🚏 Route overview" / `route_overview`) that open
  an inline parameter picker (`frontend/src/components/ParamStrip.tsx`)
  before running.
- **Bottom dock** — `frontend/src/components/QuestionDock.tsx`: persistent
  chip toolbar (visible once a thread has messages) to start a new
  templated question; renders `ParamStrip` when a chip is "composing".
- **Message thread** — `frontend/src/tabs/ask/MessageList.tsx` renders
  bubbles; assistant bubbles render `frontend/src/tabs/ask/RichResult.tsx`
  (table / kv / chart depending on `result.kind`), with a collapsible
  "details" JSON dump of tool/args/result.
- **Follow-up chips** —
  `frontend/src/tabs/ask/FollowupChipsRow.tsx`: 5 canned chips (why /
  reliability / slice / summarize / next) plus a free-text box, shown
  under the latest assistant message with a tool result. Gated by
  `ASK_FOLLOWUP_ENABLED` (`useFollowupEnabled` hook).
- There is **no free-text primary input** in the current UI — the primary
  path is entirely the 5 parameterized card/chip templates
  (`frontend/src/components/askCardTemplates.ts`). Free text only appears
  in the follow-up box, plus internally via the anonymous `__build__`
  sentinel (see below).
- Filter bar: `frontend/src/components/FilterContextBar.tsx` scopes every
  dispatch to the shared date/DOW/time-band/service/route context.

## Request path

**Primary (deterministic, no LLM) path — authenticated user:**
`AskLandingCards` / `QuestionDock` → `AskTab` dispatch handlers →
`useCreateConversation` / `useAppendMessage`
(`frontend/src/api/hooks.ts`) → `POST /api/{agency_id}/conversations` then
`POST /api/{agency_id}/conversations/{cid}/messages`
(`api/routers/conversations.py: append_message_endpoint`) →
`pipeline.query.tools.dispatch` directly — no router/LLM stages at all,
since the frontend template already supplies `tool`/`args`.

**Anonymous user, same templates:** `useAppendMessage`'s anon branch
builds a synthetic `question = "__build__ <tool> <json-args>"` and calls
`POST /api/{agency_id}/ask` (`api/routers/ask.py: ask()`). This still
passes through Stage 1 (regex `_RULES`) and Stage 2 (embedding NN) first
like any other question — both normally miss against the sentinel string,
since it looks nothing like a real question — and only when neither stage
decides does the request fall to `pipeline.query.chat.chat_with_tools`,
which recognizes the `__build__` prefix and short-circuits to parse
`(tool, args)` directly, dispatching **without ever calling the LLM**
either way.

**Free-text / natural-language path (the 3-stage router)** — reached via
`POST /api/{agency_id}/ask` for any question that isn't a `__build__`
sentinel:

1. `api/routers/ask.py: ask()` resolves `RangeCtx`, checks `is_follow_up()`
   (pagination-style phrases route straight to the LLM with history), then
   calls `pipeline/query/router.py: route_or_examples(question, conn, agency_id, k=3)`.
2. **Stage 1 — rules**: `_RULES` regex list (e.g. "遅延.*ワースト" →
   `top_n`). A match dispatches immediately via `pipeline.query.tools.dispatch`.
3. **Stage 2 — embedding NN**: no rule match → embed the question with
   `pipeline/query/embeddings.py` (`intfloat/multilingual-e5-small`,
   384-dim) and look up nearest neighbors in `rag_chunks` via
   `pipeline/query/rag_index.py: nearest()` (pgvector cosine distance). A
   top match within threshold (`_EMBED_DISPATCH_THRESHOLD = 0.12`, margin
   `0.02` over the runner-up) dispatches via that golden example's stored
   tool/args (source: `tests/ask_eval/golden_set.jsonl`).
4. **Stage 3 — RAG + LLM**: neither stage decided → top-3 golden examples
   are few-shot-injected and `pipeline/query/chat.py: chat_with_tools(...)`
   calls the provider ladder (`pipeline/query/llm_client.py`) with the
   tool-use surface from `pipeline/query/tools.py`. The ladder's order and
   membership are **env-configured**, not fixed: `CHAT_PROVIDERS` (comma
   list, `.env.example` ships `cerebras,groq`; the code's own back-compat
   default if unset is just `groq`) selects from `cerebras` / `groq` /
   `openai` / `ollama` — `openai` is a supported but optional paid rung,
   meant to be placed last. The chosen tool call is dispatched the same
   way; out-of-scope questions get a friendly refusal with suggestions.
5. All paths converge on `dispatch()` → a `_tool_*` handler in
   `pipeline/query/tools.py`. Whether that handler reads precomputed
   `agg_*` tables (Postgres) or scans live `updates` (ClickHouse) depends
   on the **specific tool**, not uniformly on filter narrowness:
   - The ranking family — `top_n`, `on_time` (`on_time_rate`), `trend`
     (`time_series`), and `compare_segments`'s `dimension="dow"` branch
     (used by the `cmp_service` alias) — follows the repo-wide pattern:
     `agg_*` (`agg_daily_trend` etc.) by default, falling back to a live
     ClickHouse scan only for a `time_band`-filtered/narrow request (see
     e.g. `compute_ranking`/`compute_trend_series` in
     `pipeline/reports/rankings.py`).
   - `route_stats` (`route_dow_breakdown`), `compare_segments`'s
     `dimension="service_type"` branch (`route_compare_service`),
     `segment_hotspots`, and `schedule_realism_segments`
     (`pipeline/query/tool_queries.py`) have **no agg fast path at all** —
     they always read live `updates` via ClickHouse regardless of the
     filter.
   Either way the result becomes a `ToolResult` →
   `render_tool_result()` produces the locale-specific summary string.
6. `ask.py` logs to `ask_query_log` via `pipeline/query/query_log.py:
   log_query()` unless `ASK_QUERY_LOG_ENABLED=false` (skipped for
   `__build__` sentinels).

**Follow-up path** (LLM-grounded, kill-switch gated):
`FollowupChipsRow` → `useFollowup` →
`POST /api/{agency_id}/conversations/{cid}/followup`
(`api/routers/conversations.py: followup_endpoint`) →
`pipeline/query/followup.py: answer_followup()` — answers strictly from
the prior tool result's serialized data (never re-dispatches a tool).

**Other Ask-adjacent endpoints** (`api/routers/ask.py`):

- `GET /ask/suggest` — autocomplete via embedding NN, or top hit-count
  chunks for an empty query.
- `GET /ask/build-schema` — metadata for a guided-builder UI form.
- `POST /ask/edit-action` — records confirm/edit verdict on a cached
  low-confidence interpretation (`ASK_INTENT_CACHE_ENABLED`).
- `GET /ask/followup-enabled` (in `api/routers/conversations.py`) —
  `{enabled, max_question_chars}`, drives `useFollowupEnabled`.

`api/routers/ask_dashboard.py` (`GET /ask/dashboard/{heatmap,anomalies,movers}`,
backed by `pipeline/dashboard_queries.py`) exists per its own docstring for
Ask-tab analysis previews, but no current file under
`frontend/src/tabs/ask/` or `AskTab.tsx` calls it — treat as
unwired/consumed elsewhere rather than assuming it's live in this UI.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/AskTab.tsx` | Ask tab shell: thread/filter state, dispatch orchestration |
| `frontend/src/tabs/ask/AskLandingCards.tsx` | Empty-thread landing cards/pills |
| `frontend/src/tabs/ask/MessageList.tsx` | Message bubble list |
| `frontend/src/tabs/ask/RichResult.tsx` | Renders table/kv/series tool results |
| `frontend/src/tabs/ask/FollowupChipsRow.tsx` | Follow-up chips + free-text box |
| `frontend/src/tabs/ask/filterCtx.ts` | `FilterCtx` <-> `RangeCtx` helpers |
| `frontend/src/components/QuestionDock.tsx` | Bottom dock state machine (idle/composing/busy) |
| `frontend/src/components/ParamStrip.tsx` | Inline parameter composer for a chip template |
| `frontend/src/components/paramPills/*.tsx` | Individual param controls (segmented/limit/route picker) |
| `frontend/src/components/askCardTemplates.ts` | Declarative chip templates (tool + args + i18n keys) |
| `frontend/src/components/askFollowupChips.ts` | Follow-up chip definitions |
| `frontend/src/components/ThreadSidebar.tsx` | Conversation list (anon localStorage ↔ server) |
| `frontend/src/components/FilterContextBar.tsx` | Date/DOW/time-band/service/route filter strip |
| `frontend/src/api/hooks.ts` | TanStack Query hooks for all `/ask` + `/conversations` endpoints |
| `frontend/src/api/conversationsAnon.ts` | localStorage-backed anon conversation store |

**Backend — API routers**

| File | Role |
|---|---|
| `api/routers/ask.py` | `POST /ask` (3-stage router entry), `/ask/suggest`, `/ask/build-schema`, `/ask/edit-action` |
| `api/routers/conversations.py` | Conversation CRUD, `POST /messages` (primary deterministic dispatch), `POST /followup`, `GET /ask/followup-enabled`, anon-migration |
| `api/routers/ask_dashboard.py` | `/ask/dashboard/{heatmap,anomalies,movers}` |

**Backend — `pipeline/query/`**

| File | Role |
|---|---|
| `router.py` | Stage 1 (regex `_RULES`) + Stage 2 (embedding NN) dispatch decision, or few-shot examples for Stage 3; `is_follow_up()` |
| `embeddings.py` | `intfloat/multilingual-e5-small` wrapper for Stage 2 + RAG index build |
| `rag_index.py` | pgvector cosine-NN reader over `rag_chunks` (`nearest()`) |
| `chat.py` | Stage 3 orchestration, `__build__` sentinel handling, intent-cache lookup/upsert |
| `llm_client.py` | `CHAT_PROVIDERS`-ordered provider ladder (cerebras/groq/openai/ollama), malformed tool-call recovery |
| `tools.py` | Tool specs (`TOOLS`), `dispatch()`, `render_tool_result()`, the `_LOCALES` string table |
| `tool_queries.py` | SQL helpers backing several tool handlers |
| `intent.py` / `intent_cache.py` | Canonical-intent signature/cache (`ASK_INTENT_CACHE_ENABLED`) |
| `conversations.py` | `ask_conversations`/`ask_conversation_messages` DB access |
| `followup.py` | LLM-grounded follow-up answerer (`ASK_FOLLOWUP_ENABLED` kill switch) |
| `labels.py` | `dow_label`/time-band display helpers |
| `meta_tools.py` | `describe_data`-style meta tools merged into the handler map |
| `query_log.py` | `ask_query_log` writer, gated by `ASK_QUERY_LOG_ENABLED` |

## How to verify manually

**Automated tests:**

- Backend router/pipeline: `tests/query/test_router.py` (rules +
  embedding stage), `tests/query/test_embeddings.py`,
  `tests/query/test_rag_index.py`, `tests/query/test_chat_confidence.py`,
  `tests/query/test_chat_intent_cache.py`,
  `tests/query/test_chat_null_args.py`,
  `tests/query/test_chat_error_leakage.py`,
  `tests/query/test_llm_client.py`, `tests/query/test_intent.py`,
  `tests/query/test_intent_cache.py`, `tests/query/test_meta_tools.py`,
  `tests/query/test_tool_queries.py`,
  `tests/query/test_tools_integration.py`,
  `tests/query/test_tools_locale.py` (pins exact `_LOCALES` strings),
  `tests/query/test_conversations.py`,
  `tests/query/test_paraphrase_collapse.py`,
  `tests/query/test_query_log.py`, `tests/query/test_schema_linker.py`.
- API-level: `tests/api/test_api_ask.py` (end-to-end `/ask` — rule-hit
  skip, ClickHouse-degrade, follow-up rerouting, query-log writes, CSRF),
  `tests/api/test_ask_endpoints.py`, `tests/api/test_ask_dashboard.py`,
  `tests/api/test_conversations.py` (includes follow-up endpoint +
  kill-switch behavior).
- End-to-end eval: `tests/ask_eval/test_ask_eval.py` is the CI gate — it
  shells out to `scripts/ask_eval.py`, which reads
  `tests/ask_eval/gold_questions.jsonl` (chip + builder coverage must be
  100%). Separately, `tests/ask_eval/test_baseline.py` (opt-in via
  `RUN_LLM_EVAL=1` + a real `GROQ_API_KEY`, hits a running dev API) replays
  `tests/ask_eval/golden_set.jsonl` against the live 3-stage router and
  scores tool-selection accuracy — `golden_set.jsonl` is also the file
  `make build-rag-index` embeds into `rag_chunks` for Stage 2 (see
  `pipeline/query/rag_index.py`, `gtfs_pipeline.py: cmd_build_rag_index`).
  These are two different JSONL files with two different jobs — don't
  conflate them.
- Most of these need both throwaway Postgres (`:5544`) and throwaway
  ClickHouse (`:8124`) — see `CLAUDE.md` ▸ Database safety for the
  `RUN_CH_INTEGRATION=1` block; omitting it silently skips
  ClickHouse-gated tests instead of failing.

**Manual click-through** (`make serve` + `make frontend-dev`, or
`make serve` alone for single-origin):

1. `cp .env.example .env`; set a real `GROQ_API_KEY` (and optionally
   `CEREBRAS_API_KEY` — Cerebras is tried first per `CHAT_PROVIDERS`) to
   exercise Stage 3. `ASK_FOLLOWUP_ENABLED=true` ships as the
   `.env.example` local-dev default, so follow-up chips work out of the
   box; set it `false` to verify the kill-switch (chips hidden,
   `POST /followup` returns 503).
2. `make bootstrap && make serve` (FastAPI on `:8000`), or add
   `make frontend-dev` for hot reload on `:5173` (Vite proxies `/api`).
   `make bootstrap` alone leaves the DB empty — load data before clicking
   through, or every card returns an empty result:
   `make fetch-ingest` (or `ingest_live` + `make load_static`), then
   `make analyze` for the agency.
3. Build the Stage 2 embedding index so paraphrases actually route to
   `"embedding"` instead of always falling to Stage 3:
   `poetry run python gtfs_pipeline.py build_rag_index --agency-id 1`
   (or `make build-rag-index` for all agencies). Without this,
   `rag_chunks` is empty and Stage 2 never dispatches.
4. Open the app — the default route lands on Overview
   (`/agencies/{id}/overview`; a fresh/remembered agency selection instead
   redirects to Map). Click "Ask" in the sidebar to reach this tab.
5. On the empty-thread landing view, click an instant card (e.g.
   "🏆 Top-N delays") — expect an immediate assistant bubble with a ranked
   table (deterministic `conversations/{cid}/messages` → `dispatch` path,
   no LLM).
6. Click a route-requiring pill (e.g. "📈 Route delay trend"), pick a
   route, then run it from the `ParamStrip` — expect a trend chart bubble.
7. After any answer, use a follow-up chip ("Why this pattern?") or type
   free text in the follow-up box — expect a short LLM-generated
   explanation grounded only in the prior table.
8. To exercise the 3-stage NL router directly (not reachable from the
   current chip-only UI), call the API directly:
   ```bash
   curl -X POST localhost:8000/api/1/ask \
     -H 'Content-Type: application/json' \
     -d '{"question":"遅延ワースト10"}'
   ```
   Expect `router_stage: "rules"`. A paraphrase near a golden-set entry
   should return `"embedding"`; a genuinely novel/out-of-scope question
   should return `"llm"` (needs a working Groq/Cerebras key) or a
   friendly refusal.
9. Toggle `ASK_ROUTER_ENABLED=false` to force every question to Stage 3,
   or `ASK_HISTORY_ENABLED=false` to disable follow-up-phrase ("もっと")
   LLM rerouting.

## i18n

- **Frontend**: all Ask-tab strings live under the `ask.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`). Notable subtrees: `ask.landing.*`, `ask.card.*`
  (including `ask.card.param.*` option labels), `ask.dock.*`,
  `ask.followup_*` / `ask.followup_chips.*` (the 5 canned chip
  label/prompt pairs), `ask.col.*` (table column headers),
  `ask.build_labels.*` (anon `__build__` summary / builder-schema UI),
  `ask.ctx.*` (filter-context chips). New templates/params need matching
  keys in both locale files.
- **Server-side**: `pipeline/query/tools.py`'s
  `_LOCALES: dict[tuple[str, str], str]` holds every user-facing string
  the tool handlers can emit, keyed `(template_name, locale)` with
  `ja`/`en` pairs, consumed via `_summary(template, lang, **vars)` (falls
  back to `ja` if the requested locale is missing). `pipeline/query/followup.py`
  has its own separate hardcoded `_SYS_PROMPT_JA`/`_SYS_PROMPT_EN` system
  prompts (not in `_LOCALES`). Locale flows from the HTTP layer
  (`api.deps.get_locale`, Accept-Language header, default `ja`) through
  `ask.py` → `dispatch(..., locale=locale)` / `chat_with_tools(...,
  locale=locale)` → every `_tool_*` handler and `render_tool_result()`.
  `tests/query/test_tools_locale.py` pins exact `_LOCALES` values, so any
  template edit must update both `ja` and `en` entries plus that test.
