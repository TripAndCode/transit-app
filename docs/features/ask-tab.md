# Notebook (the Ask tab)

The investigation notebook (English "Notebook" in the nav, 調査ノート everywhere in
Japanese, which has no short form): ready-made, deterministic analyses of an agency's
delay data, run from cards and kept as saved investigations (threads), with exports and
per-stop evidence. It does not promise free-text question answering; an optional LLM
follow-up exists, gated by its kill switch, `llm_enabled` and per-user approval (see
"Who may reach the LLM"), and is never the headline. A card is an analysis; the saved
thread is an investigation. User-facing copy says "notebook"; routes, components, endpoints, i18n keys
(`ask.*`, `nav.ask`) and tables keep the `ask` name. See
`README.md` ▸ "Data Flow" (the Ask-tab bullet) for the architecture summary
this doc expands on with file-level detail.

## How a user reaches it

- Route: `/agencies/:agencyId/ask`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded). It is **not** the default landing tab — a bare
  `agencies/:agencyId` navigates to `pulse`
  (`frontend/src/main.tsx`), and `frontend/src/components/OnboardingGate.tsx`
  redirects a fresh/remembered agency selection to
  `/agencies/{id}/pulse`.
  Reach the notebook from the rail's Notebook link, the command palette's "Notebook"
  entry, or the `g q` chord.
- Entry points: the notebook is not one of the seven destinations in
  `SIDEBAR_NAV_ITEMS`; the rail shows it as a separate dashed link below
  them (`frontend/src/components/Sidebar.tsx`), labelled by its name alone,
  with no 「乗換」 (transfer) tag, which would read as a journey planner. The top bar
  (`frontend/src/components/TopBar.tsx`) above every agency screen carries
  a search field (`topbar.ask_placeholder`) that opens the command palette
  (also ⌘K); the palette's "Go to" group lists the notebook after the rail
  destinations (`GO_TO_TARGETS` in
  `frontend/src/components/paletteNavTargets.ts`, `nav.ask` i18n key —
  "Notebook" / "調査ノート"). The bar's right side holds the data-freshness
  chip (`frontend/src/components/DataFreshness.tsx`): the agency's
  `latest_data_date` and the newest live reading, with a panel that spells
  both out (`topbar.freshness.*`). On a phone, the notebook is also one of the bottom tabs in
  `frontend/src/components/Sidebar.tsx`, beside Pulse, Routes, Live and
  More.
- Top-level component: `frontend/src/tabs/AskTab.tsx` — owns thread
  selection, the shared filter context (date range / DOW / time-band /
  service / routes), message dispatch, and anon-to-authenticated
  conversation migration on first login.

What the user sees/does:

- **Empty thread (landing state)** —
  `frontend/src/tabs/ask/AskLandingCards.tsx`, headed 「用意された分析から選ぶ」 / "Pick a ready-made analysis": the page has no box to
  type a question into, only the cards below. Each instant card's example
  answer describes the answer its default settings give (the on-time card
  lists the least on-time routes first). `buildCardTemplates()`
  (`frontend/src/components/askCardTemplates.ts`) defines the templates,
  each labelled with a line icon from the app's set, split by
  `needsRoute()` into instant-run cards that need no route ("Top-N delays"
  / `top_delay`, "On-time rate ranking" / `ontime_rank`) which dispatch
  immediately on click, and route-requiring "pills" ("Explore all stops by
  pattern" / `stop_patterns`, "Which stops have larger delays?" /
  `stop_hotspots`, "Route delay trend" / `route_trend`, "Weekday vs
  Weekend/Holiday" / `weekday_vs_weekend`, "Route overview" / `route_overview`)
  that open an inline parameter picker
  (`frontend/src/components/ParamStrip.tsx`) before running.
- **Bottom dock** — `frontend/src/components/QuestionDock.tsx`: persistent
  chip toolbar (visible once a thread has messages) to start a new
  templated question; renders `ParamStrip` when a chip is "composing".
- **Message thread** — `frontend/src/tabs/ask/MessageList.tsx` renders
  bubbles; assistant bubbles render `frontend/src/tabs/ask/RichResult.tsx`
  (table / kv / chart depending on `result.kind`), with a collapsible
  "details" JSON dump of tool/args/result.
- **Link to the question's home** — a ranking, on-time or weekday/weekend
  card ends with "Open on Routes / Day types & agencies", and a trend card
  with "See the daily trend on Time" (`frontend/src/tabs/ask/cardHome.ts`):
  the screen that answers its question in full (`analysis-tab.md` ▸ One home
  per question).
  - The link carries the scope the card was answered under, not the
    thread's current filters. The card templates send no dates, so the
    period, routes, dow, time band and service come from the message's
    `conditions`, which `_run_card` (`api/routers/conversations.py`) records
    from the dispatched filters. A card's own `from_date`/`to_date` win over
    them, as they do at dispatch; a card's own route narrows the link to that
    route; a top-N card's `service_type` replaces the service filter, mapped
    as `_SERVICE_TYPE_MAP` maps it.
  - A card saved before `conditions` kept the period has no link: a link to
    the screen's default days would show other figures.
  - Ranking links set `sparse=1`, so the Routes table ranks at
    `MIN_GROUP_SAMPLES` (21), the floor every ranking card applies, the
    5-minute one included, rather than its default 100. A ranking card over
    named routes lists a retired code or a zero-delay route it names
    (`left_out_unless_named`), so its link also sets `retired=1` and
    `zero_delay=1`. Compare's board has no such toggle, so a weekday/weekend
    card naming a retired route links to a board without it. A ranking card
    keeps its order (`best_first`), and an on-time card's `threshold_min`
    becomes the scope's late tolerance with every early departure on time,
    which is how the tool counts it.
  - Time's trend is daily, so a weekly or monthly trend card's link says
    "daily" rather than promising its own granularity.
  - The per-day-type split of one route (`compare_segments`,
    `dimension="service_type"`) has no home and no link.
- **Result exports** — `frontend/src/tabs/ask/ResultExports.tsx`: the
  selected step as JSON, and a CSV for each table answer (`resultTableCsv`
  in `resultSnapshot.ts`). Both record the filters the answer ran under
  (`historical_filter_ctx`) when its message kept them with its period, and
  say they were not recorded for an older message. The CSV opens with key/value rows recording what
  produced it, the last being `data_source`: the agency's open-data credit
  in the UI language (`useAgencyCredit`), left out when no source covers
  the agency's feed. A blank row and the table follow.
- **Follow-up box** —
  `frontend/src/tabs/ask/FollowupChipsRow.tsx`: a free-text input and send
  button, grounded on the selected stop when one is selected, otherwise on
  the latest assistant message with a tool result. An unselected
  `route_stop_patterns` result shows a select-a-stop prompt
  (`ask.evidence.select_to_ask`) in its place. Shown only when
  `ASK_FOLLOWUP_ENABLED` is on (`useFollowupEnabled` hook) and the
  signed-in caller is `llm_approved` (see "Who may reach the LLM"
  below).
- There is **no free-text primary input** in the current UI — the primary
  path is entirely the parameterized card/chip templates
  (`frontend/src/components/askCardTemplates.ts`). Free text only appears
  in the follow-up box.
- Conditions: the shared scope sentence (`ScopeSentence`; see
  `analysis-tab.md` ▸ The scope sentence) shows and edits the open thread's
  `filter_ctx`, its period, days, timetable, time band and routes, through
  `threadScope`/`patchThreadFilter` in `frontend/src/tabs/ask/filterCtx.ts`,
  which read a stored filter through the URL scope's own parser. With no
  thread open it shows the screen's URL scope, which seeds the next new
  thread (`rangeCtxToFilterCtx`). Each change saves to the open thread
  before the next dispatch reads it. A thread stores no on-time tolerance,
  so its sentence has no tolerance clause, and it carries no saved views or
  pin; its tokens are disabled while a question is being sent.

## Request path

**Primary (deterministic, no LLM) path — authenticated user:**
`AskLandingCards` / `QuestionDock` → `AskTab` dispatch handlers →
`useCreateConversation` / `useAppendMessage`
(`frontend/src/api/hooks.ts`) → `POST /api/{agency_id}/conversations` then
`POST /api/{agency_id}/conversations/{cid}/messages`
(`api/routers/conversations.py: append_message_endpoint`) →
`pipeline.query.tools.dispatch` directly — no router/LLM stages at all,
since the frontend template already supplies `tool`/`args`.

**Anonymous user (anonymous-only mode), same templates:** `useAppendMessage`'s anon branch
calls `POST /api/{agency_id}/ask/card` (`api/routers/conversations.py:
run_card_endpoint`) with `{tool, args, filter_ctx}`. It runs the same card
dispatch as the signed-in path (`_run_card`, without the intent-cache write),
stores nothing, and returns the assistant message's fields, which the browser
keeps in its localStorage thread (`frontend/src/api/conversationsAnon.ts`).

**Method stamp on saved cards:** a card whose figures a report computes carries
that report's `DefinitionMeta`, built through
`pipeline.reports.report_stamp.report_definition` (the function
`render_report` uses) before the figures, over the scope the tool computed:

| Card tool | Report whose stamp it carries |
|---|---|
| `top_n` `avg_delay` / `on_time_rate` / `worst_5min` | `ranking` / `on_time` / `worst_5min` |
| `on_time` (`on_time_rate`) | `on_time`; a `threshold_min` is the window "up to N min late", read by `compute_on_time`'s own rule |
| `trend` (`time_series`) | `trend` as exported (`format=csv`): the daily series alone |
| `cmp_service` (`compare_segments`, `dimension="dow"`) | `compare_ranking` |

Every other tool computes figures no report does and carries no stamp. A
signed-in card stores it in `ask_conversation_messages.definition`; a
signed-out card keeps the copy `/ask/card` returned, which the sign-in
migration validates as a `DefinitionMeta` and stores, or drops. A card saved
before the column existed reads `NULL` and shows no stamp. The evidence card
renders it with the reports' `DefinitionMetaBlock`.

**Free-text / natural-language path (rules, then the LLM)** — reached via
`POST /api/{agency_id}/ask`:

1. `api/routers/ask.py: ask()` resolves `RangeCtx`, checks `is_follow_up()`
   (follow-up phrases route straight to the LLM with history) and, with no
   history, `needs_previous_turn()`, which gives the `no_history` prompt to
   the follow-ups a first turn cannot answer: a reference back ("さっき",
   "同じ条件") always; continuation ("もっと", "次の50件") unless the router
   is on and a rule answers it; and refinement ("降順", "絞り込",
   "さらに", "別の") unless it names its own subject (a transit noun or
   metric such as 路線, 停留所, 遅延 or 定時率, or a route code; "その路線"
   does not count) or such a rule answers it. The rest are routed as usual, and with the router off
   go to the LLM. Then it checks
   `needs_history()` (with history, a question that points back at the
   conversation, "その路線は？", and names no route of its own also goes
   straight to the LLM; with no history it is routed as usual), then
   calls `pipeline/query/router.py: match_rules(question)`.
2. **Rules**: `_RULES` regex list (e.g. "遅延.*ワースト" →
   `top_n`). A match dispatches immediately via `pipeline.query.tools.dispatch`.
3. **LLM stage**: no rule matched →
   `pipeline/query/chat.py: chat_with_tools(...)` calls the provider ladder (`pipeline/query/llm_client.py`) with the
   tool-use surface from `pipeline/query/tools.py`. The ladder's order and
   membership are **env-configured**, not fixed: `CHAT_PROVIDERS` (comma
   list, `.env.example` ships `gemini,openai`; the code's own back-compat
   default if unset is just `gemini`) selects from `gemini` / `openai` —
   `openai` is a supported but optional paid rung, meant to be placed
   last. The chosen tool call is dispatched the same
   way; out-of-scope questions get a friendly refusal with suggestions.
   The API starts with no provider key: the ladder is then empty, a request
   that needs the LLM answers the "no AI provider is configured" message,
   follow-ups report disabled (`POST .../followup` returns 503), and
   `/api/config` reports `llm_enabled: false`, which hides the follow-up box.
   The notebook's card dispatch (signed in and `/ask/card`), threads and
   exports need no key.
4. All paths converge on `dispatch()` → a `_tool_*` handler in
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
5. `ask.py` logs to `ask_query_log` via `pipeline/query/query_log.py:
   log_query()` unless `ASK_QUERY_LOG_ENABLED=false`.

**Usage log.** Each notebook card dispatch writes one `ask_query_log` row, on
the same kill switch: `router_stage` is `card_signed_in`
(`POST .../conversations/{cid}/messages`) or `card_signed_out`
(`POST .../ask/card`), `tool` is the canonical handler name (an unknown or
aliased client name resolves to the handler's, or NULL), `success` is false for
an empty or failed dispatch, and `latency_ms` is the dispatch's own wall time
(a signed-in card counts as a success only once its thread's transaction has
committed). A follow-up writes a `followup` row naming the tool of the result
it used (NULL when that answer came from no tool), with `latency_ms` of the
model call and the numeric-guard verdict in `numeric_guard_triggered`. The
`question` column is empty on every notebook row: the follow-up's text stays in
the user's own conversation. Notebook rows carry no user id, conversation id,
arguments or text. The write
follows the request's transaction and swallows its own failure, so logging
cannot fail a card or lose a follow-up's answer. The admin Ask ops page reports these as per-tool
counts over the trailing `DEFAULT_RANGE_DAYS` JST days.

**Follow-up path** (LLM-grounded, kill-switch gated):
`FollowupChipsRow` → `useFollowup` →
`POST /api/{agency_id}/conversations/{cid}/followup`
(`api/routers/conversations.py: followup_endpoint`) →
`pipeline/query/followup.py: answer_followup()` — answers strictly from
the prior tool result's serialized data (never re-dispatches a tool).

**Other Ask-adjacent endpoints:** `GET /ask/followup-enabled` (in
`api/routers/conversations.py`) — `{enabled, max_question_chars}`, drives
`useFollowupEnabled`.

## Who may reach the LLM

Reaching an LLM requires a signed-in caller whose `users.llm_approved` flag an
admin has set. The two LLM-backed surfaces enforce it in different places, for
one structural reason:

| Surface | Where the gate runs | Rejected caller sees |
| --- | --- | --- |
| `POST /conversations/{id}/followup` | `require_llm_approved(user)` at the top of the handler (`api/security.py`) | `403 llm_not_approved` |
| `POST /ask` | inside `chat_with_tools`'s `_call_llm` (`pipeline/query/chat.py`), via the `llm_approved` argument the router threads in | `200` with an honest-degradation answer |

`/ask` cannot reject the whole request upfront the way the follow-up does:
The regex rules answer many
questions with no LLM at all, and those stay open to every caller the login gate admits (everyone in anonymous-only mode). So the flag
travels into the orchestrator and short-circuits only the LLM call,
returning `error_kind="not_approved"` before any provider or BYOK key is
touched. Anonymous callers have no user row, so the router passes
`llm_approved=False` for them — they get the rules and nothing more.

Admins toggle the flag from `PATCH /api/admin/users/{uid}`, which writes an
`llm_approved_changed` row to `login_events` whenever the value actually
changes.

Approval is the whole budget control: an approved caller's LLM calls are
bounded only by the generic per-minute limiter every route shares
(`FREE_LIMIT`/`PRO_LIMIT` in `api/middleware/ratelimit.py`), not by any
per-day LLM allowance. Metering approved callers would key on the user row,
so it is a separate mechanism to build, not a knob to turn on.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/AskTab.tsx` | Ask tab shell: thread/filter state, dispatch orchestration |
| `frontend/src/tabs/ask/AskLandingCards.tsx` | Empty-thread landing cards/pills |
| `frontend/src/tabs/ask/MessageList.tsx` | Message bubble list |
| `frontend/src/tabs/ask/RichResult.tsx` | Renders table/kv/series tool results |
| `frontend/src/tabs/ask/FollowupChipsRow.tsx` | Follow-up free-text box |
| `frontend/src/tabs/ask/filterCtx.ts` | `FilterCtx` <-> `RangeCtx` helpers |
| `frontend/src/components/QuestionDock.tsx` | Bottom dock state machine (idle/composing/busy) |
| `frontend/src/components/ParamStrip.tsx` | Inline parameter composer for a chip template |
| `frontend/src/components/paramPills/*.tsx` | Individual param controls (segmented/limit/route picker) |
| `frontend/src/components/askCardTemplates.ts` | Declarative chip templates (tool + args + i18n keys) |
| `frontend/src/components/ThreadSidebar.tsx` | Conversation list (anon localStorage ↔ server) |
| `frontend/src/tabs/ask/filterCtx.ts` | A thread's `filter_ctx` as the scope the scope sentence reads and writes |
| `frontend/src/api/hooks.ts` | TanStack Query hooks for all `/ask` + `/conversations` endpoints |
| `frontend/src/api/conversationsAnon.ts` | localStorage-backed anon conversation store |

**Backend — API routers**

| File | Role |
|---|---|
| `api/routers/ask.py` | `POST /ask` (rules, then LLM) |
| `api/routers/conversations.py` | Conversation CRUD, `POST /messages` (primary deterministic dispatch), `POST /followup`, `GET /ask/followup-enabled`, anon-migration |

**Backend — `pipeline/query/`**

| File | Role |
|---|---|
| `router.py` | Regex `_RULES` dispatch decision (`match_rules()`); `is_follow_up()`, `needs_previous_turn()`, `needs_history()` |
| `chat.py` | LLM-stage orchestration, intent-cache lookup/upsert |
| `llm_client.py` | `CHAT_PROVIDERS`-ordered provider ladder (gemini/openai) |
| `tools.py` | Tool specs (`TOOLS`), `dispatch()`, `render_tool_result()`, the `_LOCALES` string table |
| `tool_queries.py` | SQL helpers backing several tool handlers |
| `intent.py` / `intent_cache.py` | Canonical-intent signature/cache (`ASK_INTENT_CACHE_ENABLED`) |
| `conversations.py` | `ask_conversations`/`ask_conversation_messages` DB access |
| `followup.py` | LLM-grounded follow-up answerer (`ASK_FOLLOWUP_ENABLED` kill switch) |
| `labels.py` | `dow_label`/time-band display helpers |
| `meta_tools.py` | `describe_data`-style meta tools merged into the handler map |
| `query_log.py` | `ask_query_log` writer (router, card and follow-up rows), gated by `ASK_QUERY_LOG_ENABLED` |

## How to verify manually

**Automated tests:**

- Backend router/pipeline: `tests/query/test_router.py` (rules),
  `tests/query/test_chat_intent_cache.py`,
  `tests/query/test_chat_null_args.py`,
  `tests/query/test_chat_error_leakage.py`,
  `tests/query/test_llm_client.py`, `tests/unit/test_intent.py`,
  `tests/query/test_intent_cache.py`, `tests/query/test_meta_tools.py`,
  `tests/query/test_tool_queries.py`,
  `tests/query/test_tools_integration.py`,
  `tests/query/test_tools_locale.py` (pins exact `_LOCALES` strings),
  `tests/query/test_conversations.py`,
  `tests/unit/test_paraphrase_collapse.py`,
  `tests/query/test_query_log.py`, `tests/query/test_schema_linker.py`.
- API-level: `tests/api/test_api_ask.py` (end-to-end `/ask` — rule-hit
  skip, ClickHouse-degrade, follow-up rerouting, query-log writes, CSRF),
  `tests/api/test_conversations.py` (includes follow-up endpoint +
  kill-switch behavior).
- Gold-set canonicalization gate: `tests/ask_eval/test_ask_eval.py` shells
  out to `scripts/ask_eval.py`, which reads
  `tests/ask_eval/gold_questions.jsonl` and checks that each entry's
  expected args are unchanged by `canonicalize` (it never reads the
  `question` text, so it is not routing coverage).
- Most of these need both throwaway Postgres (`:5544`) and throwaway
  ClickHouse (`:8124`) — see `AGENTS.md` ▸ Tests for the
  `RUN_CH_INTEGRATION=1` block; omitting it silently skips
  ClickHouse-gated tests instead of failing.

**Manual click-through** (`make serve` + `make frontend-dev`, or
`make serve` alone for single-origin):

1. `cp .env.example .env`; set a real `GEMINI_API_KEY` (and optionally
   `OPENAI_API_KEY` as the paid fallback per `CHAT_PROVIDERS`) to
   exercise the LLM stage; with no key the API still boots. `ASK_FOLLOWUP_ENABLED=true` ships as the
   `.env.example` local-dev default, so the follow-up box works out of the
   box for a signed-in user an admin has marked `llm_approved`; set it
   `false` to verify the kill-switch (box hidden, `POST /followup` returns
   503).
2. `make bootstrap && make serve` (FastAPI on `:8000`), or add
   `make frontend-dev` for hot reload on `:5173` (Vite proxies `/api`).
   `make bootstrap` alone leaves the DB empty — load data before clicking
   through, or every card returns an empty result:
   `make fetch-ingest` (or `make load_static` before `ingest_live`), then
   `make analyze` for the agency.
3. Open the app — the default route lands on Pulse
   (`/agencies/{id}/pulse`; a fresh/remembered agency selection redirects
   there too). Click the top bar's search field (or press ⌘K), choose "Notebook",
   and expect `/agencies/{id}/ask`; pressing `g` then `q` goes there directly.
4. On the empty-thread landing view, click an instant card (e.g.
   "Top-N delays") — expect an immediate assistant bubble with a ranked
   table (deterministic `conversations/{cid}/messages` → `dispatch` path,
   no LLM).
5. Click a route-requiring pill (e.g. "Route delay trend"), pick a
   route, then run it from the `ParamStrip` — expect a trend chart bubble.
6. After any answer (for "Explore all stops by pattern", after selecting a
   stop), type a question in the follow-up box and send it — expect a
   short LLM-generated explanation grounded only in the prior result.
7. To exercise the NL router directly (not reachable from the
   current template-only UI), call the API directly:
   ```bash
   # `csrf_guard` rejects a POST with no Origin header (403 "origin required").
   # While sign-in is required, also pass a session cookie from a signed-in
   # browser; see the README's API example.
   curl -X POST localhost:8000/api/1/ask \
     -H 'Content-Type: application/json' \
     -H 'Origin: http://localhost:8000' \
     -H 'Cookie: sid=<your session cookie>' \
     -d '{"question":"遅延ワースト10"}'
   ```
   Expect `router_stage: "rules"`. A genuinely novel/out-of-scope question
   should return `"llm"` (needs a working Gemini/OpenAI key) or a
   friendly refusal.
8. Toggle `ASK_ROUTER_ENABLED=false` to force every question to the LLM stage,
   or `ASK_HISTORY_ENABLED=false` to disable follow-up-phrase ("もっと")
   LLM rerouting.

## i18n

- **Frontend**: all Ask-tab strings live under the `ask.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`). Notable subtrees: `ask.landing.*`, `ask.card.*`
  (including `ask.card.param.*` option labels), `ask.dock.*`,
  `ask.followup_*` (the follow-up box's placeholder and send label),
  `ask.evidence.*` (stop-evidence views and the follow-up box's
  selected-stop context), `ask.col.*` (table column headers),
  `ask.build_labels.*` (a card's fallback user-bubble summary / builder-schema UI).
  New templates/params need
  matching keys in both locale files.
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
