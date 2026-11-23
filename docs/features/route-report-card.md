# Route report card

One grade, A to F, per route per calendar month, from figures the app already
computes, with the figures behind it, their intervals and what would raise it.
The methodology states the rules for readers
([en](../methodology.en.md#route-report-card), [ja](../methodology.md#系統の成績表));
this page holds the thresholds, their sources and where each piece lives.

The card, the list and both endpoints are for admins only (`require_admin`,
and `useIsAdmin` on screen): a grade waits, as the feed-quality grade does, for
the methodology's publication process, operators' advance notice and right of
reply. `tests/unit/test_agency_feed_quality.py` holds every response carrying a
`grade` to that.

## Where it is

- The card: the route's own page (`frontend/src/components/reportCard/RouteReportCard.tsx`,
  rendered by `RouteAnalysisTab`). It picks a closed month (`card_month`, the
  newest closed month when unset), shows the grade, every figure with its
  letter and its 95% interval (excess wait and bunching have none), the last three months, what would raise the grade and
  the definition stamp. "Print card" prints the card alone on one page.
- The list: **Routes** → "Report cards" (`by=report_cards`, the lazy
  `frontend/src/tabs/reportCards/ReportCardsView.tsx`), one row per route with
  its grade or why it has none, and a CSV.
- Both read their own month and none of the shared scope: the responses'
  `scope_applied` is false for every field, so the route page's scope bar
  offers nothing on the card's account, and the list, like the other views with
  their own controls, shows no scope bar.
- API (`api/routers/report_cards.py`):
  - `GET /api/{agency_id}/routes/{route}/report-card?month=YYYY-MM`: the card
    and its three-month history;
  - `GET /api/{agency_id}/report-cards?month=YYYY-MM[&format=csv]`: every
    route's card. The CSV opens with the reports' definition preamble (value
    basis, methodology URL, 算出方法=), a line stating the grading rules, and
    the data-source credit.
  - A month must be closed: every one of its days has closed. An open month
    answers 422 `month_not_closed` with the newest closed month.
  - Every read is of the Postgres aggregates, none of ClickHouse, so a month
    before the retention floor still has a card from the aggregates kept for
    it, with bunching not measured where its days were never rebuilt.

## How it is computed

`pipeline/reports/report_card.py` computes every route of an agency for one
month at read time, from the aggregates, cached per agency and evicted when
analyze rebuilds that agency. The card's earlier months are the same
computation for those months. It reuses:

| Figure | Source |
|---|---|
| On-time rate, early-departure rate, observations | `compute_route_table` under `standard_m1_p5` |
| Excess waiting time, bunching | `compute_headway_quality_report`, over the days the route is high-frequency on |
| Service delivered, planned trips, trips observed | `compute_service_delivered_by_route`, judged by `delivered_reading` |
| Coverage and on-time interval | `comparability.assess` on the route's own trips |
| Method stamp | `report_definition(..., "route_table", ...)` |

Bunching is stored per route-day in `agg_route_headway_daily`
(`bunched_count`, `bunching_judged`, migration 0112) by
`pipeline.headways.count_bunched`: gaps strictly shorter than
`BUNCHING_HEADWAY_FRACTION` (0.25) of the day's own timetable's scheduled
median. A day without that median, a row built before the columns, and a day
before the retention floor (never rebuilt) keep NULL counts, and a month with
one such pooled day shows bunching as not measured.

Bunching carries no interval. It pools only the days the route is
high-frequency on and of its dominant day type, and the trips behind those days
are not counted apart from the month's, so counting the month's trips would
print an interval narrower than the figure supports. Excess wait has none
because the pooled sums it comes from cannot give its spread.

## Thresholds and sources

All in `pipeline/route_grade.py`. Every edge is inclusive, and each figure is
judged as printed, to a tenth.

| Criterion | A | B | C | D | F | Source |
|---|---|---|---|---|---|---|
| Excess waiting time (high-frequency) | ≤ 0.5 min | ≤ 1.0 | ≤ 1.5 | ≤ 2.0 | > 2.0 | A and F: this app's defaults; TfL's high-frequency target of 1.1 min falls in C |
| Bunching (high-frequency) | ≤ 5% | ≤ 10% | ≤ 15% | ≤ 20% | > 20% | A: the Bus Turnaround target of 5% on frequent routes |
| On-time rate, `standard_m1_p5` (low-frequency) | ≥ 90% | ≥ 80% | ≥ 70% | ≥ 60% | < 60% | Bus Turnaround's 75% target falls in C, TfL's 86% in B |
| Early-departure rate (low-frequency) | ≤ 1% | ≤ 2% | ≤ 5% | ≤ 10% | > 10% | this app's own; no public scheme grades it separately |
| Service delivered (any class) | ≥ 95%: no cap | | | < 95%: D at best | < 90% | adapted from Bus Turnaround's dropped-trips ("truancy") fail |

The B, C and D edges of excess wait, bunching and on-time split the span
between the A and F edges evenly. A high-frequency route takes the worse of its
excess wait and bunching letters, a low-frequency route the worse of its
on-time and early-departure letters, and service delivered then caps it.

Sources:

- Bus Turnaround report cards, TransitCenter and the Straphangers Campaign:
  <https://busturnaround.nyc/report-cards>,
  <https://transitcenter.org/nyc-bus-turnaround-report-card/>. Bunching is a
  bus arriving less than 25% of the scheduled headway after the one before; an
  F on any graded figure fails the route; dropped trips over 5% fail it.
- NYC Comptroller, "Life in the Slow Lane" (2025):
  <https://comptroller.nyc.gov/reports/life-in-the-slow-lane>.
- TfL buses performance data (excess wait target on high-frequency routes,
  on-time target on low-frequency routes):
  <https://tigris.tfl.gov.uk/corporate/publications-and-reports/buses-performance-data>.

The observation floor is the Routes table's ranking floor (100,
`RANKING_MIN_SAMPLES`), the floor the performance standards judge a route's
on-time and service-delivered figures at, rather than the ranking report's
21 (`MIN_GROUP_SAMPLES`), which only orders percentiles.

The comparability check is the agencies board's (`comparability.assess`), read
on the route's own trips: coverage of at least 70% of planned trips, and an
on-time interval no wider than 3.0 points either side, counting trips. A route
with a few hundred trips a month can fail the interval check and show N/A. The
clamp check reads an agency-wide figure, so it does not decide a route. Nor
does the value-basis check (`predicted_basis`): it judges whether agencies may
be ordered against one another, not whether a route meets the standards, so
a route on the feed's predicted delays is still graded.

## Not in this slice

- Per-agency thresholds: every agency is graded on the defaults above.
- Persisted monthly cards (a `route_report_cards` table): the grade is computed
  on read, under the current rules.
- The council report's route table and an operator league table built from
  the cards.
- A QR code to the live card on the print.
