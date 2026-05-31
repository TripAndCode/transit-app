# Playwright Smoke Tests — Phase ③.6

Run date: 2026-05-31  
Environment: anon (no login), agency_id=1, locale=EN then JA  
Backend: localhost:8000, Frontend: localhost:5173

## Scenarios

[✓] S01_empty_state — cards visible at top, sidebar shows "No conversations yet", empty hint text displayed, no thread  
[✓] S02_top_n_k5_all — top_n k=5 service=All: table renders (no JSON leak), user bubble "Top 5 routes (All)", 5 followup chips visible  
[✓] S03_followup_chip_why — clicked "Why this pattern?": user bubble shows EN prompt, assistant bubble shows real LLM answer  
[✓] S04_followup_chip_reliability — clicked "Is the sample size reliable?": LLM answer references sample counts per route  
[✓] S05_followup_chip_slice — clicked "Other slices?": LLM answer compares weekday vs weekend averages  
[✓] S06_followup_chips_summarize_next — "Summarize in 3 points" + "What to look at next?" both return real LLM answers  
[✓] S07_collapse_expand_panel — clicking strip chip expands full card panel; ▴ Close question panel button visible  
[✓] S08_close_panel — clicking ▴ Close question panel collapses back to strip  
[✓] S09_top_n_k3_weekday — changed k=3, service=Weekday, run: new bubble "Top 3 routes (Weekday)" added; chips relocate to new last result  
[✓] S10_chips_only_on_last_result — after second card run, old result bubble has NO chips; only new last result has chips  
[✓] S11_ontime_rank_card — on_time_rank "Worst first, 5 routes": table renders, new bubble added, chips on new last result only  
[✓] S12_route_trend_card — route_trend A1 Week: new bubble "Route 10012 — Week buckets" with chart and chips  
[✓] S13_weekday_vs_weekend_card — cmp_service A1: new bubble "Route 10012: weekday vs weekend comparison" with chips  
[✓] S14_route_overview_card — route_stats A1: new bubble "Route 10012 overview" with chips; all 5 cards exercised  
[✓] S15_i18n_ja_switch — switch to 日本語: nav labels, card strip labels, filter bar all in JA; chip labels switch to JA  
[✓] S16_ja_chip_labels — JA chips: "なぜこの結果？" / "観測数は信頼できる？" / "他の切り口で見ると？" / "3点に要約" / "次に見るべきは？"  
[✓] S17_ja_chip_sends_ja_prompt — click "なぜこの結果？" in JA: user bubble shows JA prompt, LLM responds in Japanese  
[✓] S18_details_disclosure_opens — ▶ 詳細 opens; pre block shows raw JSON with tool/args/result; no layout break  
[✓] S19_filter_mid_thread — Edit filter → Weekdays only → Apply: filter bar shows "Last 30 days ▸ Weekdays only"; next card run uses updated filter  
[✓] S20_no_js_errors — browser console: zero JS errors throughout session; only expected 401 /api/me (anon path)  
[✓] S21_anon_followup_no_401 — all chip clicks return 200 (anon path uses inline context body); no auth errors on followup endpoint  

## Summary

21/21 scenarios pass. No P0 bugs found.

## Fixes applied during testing

None — all scenarios passed on first run.
