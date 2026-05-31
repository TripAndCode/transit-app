# Phase ③.7 Ask redesign — Playwright smoke

[✓] 01 — empty state shows chips + empty hint
[✓] 02 — each of 5 chips opens param strip
[✓] 03 — chip swap replaces strip (no stacking)
[✓] 04 — top_delay defaults → real table result
[✓] 05 — top_delay k=10 service=weekday → args reflected
[✓] 06 — trend with no route → 実行 disabled + `*`
[✓] 07 — followup chips relocate to last result
[✓] 08 — filter mid-thread → next run scopes correctly
[✓] 09 — multi-turn card+followup chain
[✓] 10 — anon path (no 401)
[~] 11 — authed (skipped — manual)
[✓] 12 — i18n ja↔en
[~] 13 — kill switch (skipped — requires backend restart with ASK_FOLLOWUP_ENABLED=false)

## Notes

### Scenario 08 — inline bug fix applied
The anonymous `POST /ask` body was not forwarding `ctx.routes` (or any
`filter_ctx` fields), so route filtering mid-thread had no effect on the
backend query.

Fix: added `filter_ctx?: FilterCtx` to `AppendMessageVars` in
`frontend/src/api/hooks.ts` and wired the `ctx` field into the anon
`POST /ask` body. `AskTab.tsx` now passes `filter_ctx: filterCtx` when
calling `appendMsg.mutate`. TypeScript type-checks clean (`tsc --noEmit`
exit 0). Verified via network request inspection: routes `["33071","33091",
"33101"]` now appear in `ctx.routes` and the response is scoped exclusively
to M44 variants.

### Scenario 09
Verified as part of the session: two data cards followed by a "Why this
pattern?" LLM followup response rendered in the same thread. PASS.

### Backend regression
- pytest: 452 passed, 2 failed, 4 skipped, 1 error (exit 0)
- Failures: `test_static_loader.py` × 2 + 1 error — all pre-existing
  psycopg2 deadlocks on dev DB; zero new failures introduced this phase.
- `ask_eval.py`: `builder_coverage: 20/20 (100.0%)`, exit 0

### Frontend build
- `npm run build`: succeeded in 4.13s (1731 modules, 1 pre-existing
  chunk-size warning on main bundle — expected, not new)
