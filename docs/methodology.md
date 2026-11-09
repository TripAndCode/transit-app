# 算出方法と公表方針

## 変更履歴

<!-- method-changelog:start -->
<!-- poetry run python scripts/render_method_changelog.py が METHOD_VERSIONS から書く。手で編集しない -->

現在の算出方法は `2026.11.4` です。新しい版から順に、各版が設定した入力を示します。

| 版 | 設定した入力 |
|---|---|
| `2026.11.4` | `agg_daily_trend` 2 → 3, `agg_hour_daily` 3 → 4, `agg_route_daily` 2 → 3, `agg_route_daily_dist` 2 → 3, `agg_route_daily_dwell_run` 2 → 3, `agg_route_headway_daily` 2 → 3, `agg_route_hour` 3 → 4, `agg_route_hour_dow` 3 → 4, `agg_route_stats` 2 → 3, `agg_route_stop_daily` 3 → 4, `agg_service_delivered_daily` 1 → 2, `agg_stop_daily` 3 → 4, `route_delay_counts` 3 → 4 |
| `2026.11.3` | `agg_daily_trend` 1 → 2, `agg_hour_daily` 2 → 3, `agg_route_daily` 1 → 2, `agg_route_daily_dist` 1 → 2, `agg_route_daily_dwell_run` 1 → 2, `agg_route_headway_daily` 1 → 2, `agg_route_hour` 2 → 3, `agg_route_hour_dow` 2 → 3, `agg_route_stats` 1 → 2, `agg_route_stop_daily` 2 → 3, `agg_schedule_revision_daily` 1 → 2, `agg_stop_daily` 2 → 3, `agg_stop_routes` 1 → 2, `route_delay_counts` 2 → 3, `stop_route_days` 1 → 2 |
| `2026.11.2` | `agg_feed_health` 1 → 2 |
| `2026.11.1` | `agg_hour_daily` 1 → 2, `agg_route_hour` 1 → 2, `agg_route_hour_dow` 1 → 2, `agg_route_stop_daily` 1 → 2, `agg_stop_daily` 1 → 2, `route_delay_counts` 1 → 2 |
| `2026.11.0` | `MAX_PLAUSIBLE_DELAY_SEC` = 7200, `agg_daily_trend` = 1, `agg_feed_health` = 1, `agg_hour_daily` = 1, `agg_route_daily` = 1, `agg_route_daily_dist` = 1, `agg_route_daily_dwell_run` = 1, `agg_route_headway` = 1, `agg_route_headway_daily` = 1, `agg_route_hour` = 1, `agg_route_hour_dow` = 1, `agg_route_stats` = 1, `agg_route_stop_daily` = 1, `agg_schedule_revision_daily` = 1, `agg_service_delivered_daily` = 1, `agg_stop_daily` = 1, `agg_stop_routes` = 1, `route_delay_counts` = 1, `stop_route_days` = 1 |

<!-- method-changelog:end -->
