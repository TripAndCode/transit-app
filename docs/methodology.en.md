# Methodology and publication policy

## About this document

This document sets out two things about the figures Delay Dashboard ("the Service") produces: what they are computed from and by which rules, and how an operator is told before its figures are published and how an error is reported and corrected.

- **Readers**: municipal staff preparing material for a regional public-transport council (地域公共交通協議会), the members and researchers who read that material, and the operators the figures name.
- **Version**: each CSV fetched with "Download CSV", each council report and each delay record names the method version its figures were made with (for example `2026.11.4`; see [Definition stamp](#definition-stamp)). The [Changelog](#changelog) at the end shows what each version changed. The text describes the current version listed there.
- **Language**: the Japanese version is [methodology.md](methodology.md). Where the two differ, the Japanese version is authoritative.
- **Implementation detail**: the developer-facing explanation is [DELAY_ANALYSIS.md](../DELAY_ANALYSIS.md).

## Data

### Sources and licences

The Service uses only the GTFS (timetable) and GTFS-Realtime feeds the operators publish. It uses nothing from the operators' internal systems.

| Operator | Source | Licence | Poll interval |
|---|---|---|---|
| 青森市バス (Aomori City Bus) | 青森市企業局交通部, 「青森市営バス路線情報」 and 「【リアルタイム情報】青森市営バス路線情報」 (the realtime data fetched from the Public Transportation Open Data Center's public API) | CC BY 4.0 | 30 s |
| 広島電鉄, 広島バス, 広島交通, 芸陽バス, 備北交通, エイチ・ディー西広島, フォーブル, JRバス中国, ささき観光, 呉市生活バス, 廿日市市自主運行バス, おのみちバス, 朝日交通 | 公益社団法人広島県バス協会 (Hiroshima Bus Association), 「広島県バス協会GTFSオープンデータサイト」 | CC0 | 60 s |

The credit is shown in the app's data-sources section, in the preamble of each CSV fetched with "Download CSV", and at the end of a printed page.

### How the data is collected

- **Realtime data**: a collector polls each operator's TripUpdates at the interval above and keeps every poll.
- **Timetables**: fetched once a day, each version kept.
- **Two paths for observations**:
  - **Archive**: every poll the collector made.
  - **Live**: only what the app received in real time, which is sparser than the archive.
  - The figures' stamp says, day by day, which path the day's figures were computed from ([Definition stamp](#definition-stamp)).
- **Dates and times**: every date and time is Japan Standard Time (JST).

### Value basis and validation status

Every figure the Service produces is computed from **the delay the operator's feed last predicted for each stop**.

- It is not an observation of the bus passing the stop.
- The Service does not compute the delay; it is the feed's own value, which the Service collects and summarises.
- The figures' stamp names the value basis as feed prediction (`feed_prediction`).

How far the predictions are from what the buses did is checked by reconstructing stop passages from the vehicles' positions (VehiclePositions). Each operator's status:

| Operator | Validation status |
|---|---|
| 広島電鉄, 広島バス, 広島交通 | Position collection has started. After 7 to 14 days of positions, the predictions are compared with them. The comparison is not done yet. |
| Every other operator | Not validated, and no positions collected. |

An operator's result will be referenced from this document once its validation is done. Depending on the result, that operator's stamp and its treatment in comparisons may change. Until then, read every operator's figures as resting on feed predictions.

### Feed quality

A monthly feed-quality scorecard per operator is kept (from method `2026.11.5`) and shown in each operator's entry on the admin pages. Each month gives:

- **Trips reported**: the share of the trips the timetable planned that the feed reported a plausible departure for, by the same rule as the coverage in the [Comparability conditions](#comparability-conditions);
- **Stop events reported**: the share of the stops the planned trips call at that the feed reported;
- **Message age**: per poll, the time it was captured minus the feed header's timestamp (the median and the 95th percentile). A poll from the collector's archive is timed by its file name, which records when the poll began; a poll whose name records no time is left out and counted (from method `2026.11.8`);
- **Polls over 65 s**: of the polls message age measures, the share whose message was more than 65 seconds old when captured. This is the freshness threshold used by Cal-ITP and by the MobilityData GTFS-RT validator (warning W008). It shows a feed header that stayed frozen for too short a time to move the 95th percentile (from method `2026.11.12`);
- **Implausible readings**: the share of reports dropped as in [Excluding implausible values](#excluding-implausible-values);
- **Field coverage**: for each of four fields separately, the share of reports carrying that field; the four are never combined into one figure:
  - the stop ID (`stop_id`);
  - the arrival delay (`arr_delay`);
  - the trip's status (the trip's `schedule_relationship`);
  - the stop's status (the stop's `schedule_relationship`).

A figure the feed or the Service cannot supply is left empty beside a reason, never shown as 0. Trips reported and the implausible share are also stated in every figure's [definition stamp](#definition-stamp), over that figure's own period. The definitions in full are in [DELAY_ANALYSIS.md's "Feed-quality scorecard"](../DELAY_ANALYSIS.md#feed-quality-scorecard).

Each month is graded A to F from these figures by four criteria, each passing or failing on its own. Each threshold is inclusive, except the arrival-delay band in field coverage, which excludes both its ends: 0% and 50% fail. The implausible share is judged as printed to a tenth, as in the comparability conditions, and every other figure as it is shown:

| Criterion | Passes when | Basis |
|---|---|---|
| Trips reported | at least 95% | Cal-ITP's realtime completeness bar: no more than 5% of planned trips missing. Over 100% passes. The 70% in the [Comparability conditions](#comparability-conditions) answers a different question: whether delays can be ranked |
| Polls over 65 s | at most 5% | Cal-ITP's freshness threshold and the validator's W008, with Cal-ITP's 5% as the tolerance |
| Implausible readings | at most 5% | The same condition as in the [Comparability conditions](#comparability-conditions) |
| Field coverage | the stop ID and both statuses on at least 99% of reports, and the arrival delay on some but fewer than half of them | The rule the Service uses to confirm that a feed carries the fields its figures need. A feed sending the arrival delay on every report is not sending the sparse arrival estimate the Service reads it as, so it does not pass |

- A month meeting all four criteria is an A. Each criterion missed costs one letter: B, C, D, and F for all four.
- A month missing a figure that a criterion needs is not graded rather than given a guessed grade, and the reason is shown.
- Stop events reported and the message-age median and percentile are shown beside the grade, but do not affect it.
- The thresholds start from those of Cal-ITP, the California programme that publishes a monthly GTFS quality report per operator. The grade is recomputed from the stored figures, under the current thresholds, each time it is shown. Changing a threshold changes no aggregate, so it is not a new method version. A change is announced by amending this section.
- **Shown to admins only.** The grade is not published. Publishing it to operators waits on the [advance notice](#advance-notice) to each operator and the operator's right to reply.

## Processing

Under each rule's heading is the method version that introduced it.

### Deduplication and withdrawal

(the last report deciding: `2026.11.0`; withdrawal by a cancellation or skip: `2026.11.4`)

A feed sends its prediction for the same trip and stop many times. For each trip, stop and service day, the stop's departure is therefore decided by **the last report received** among these:

- a report of a delay in the plausible range: the stop departed with that delay;
- a report of the trip cancelled (`CANCELED`) or the stop skipped (`SKIPPED`): the stop had no departure, and any prediction before it is withdrawn. A prediction received after it restores the departure.

A report carrying neither a delay nor a cancelled or skipped status counts neither way: the report before it stands.

**A trip not run** is one of these:

- a trip whose last status is `CANCELED`;
- a trip whose every stop reported with a delay or a status was last reported `SKIPPED`.

A trip that skipped only some stops ran. A trip reported cancelled without listing any stop also counts as not run. Its cancellation withdraws the predictions received before it at each stop the timetable schedules the trip at whose scheduled departure was still to come when the cancellation arrived; a stop whose scheduled departure had passed keeps its departure (`2026.11.6`). The scheduled time decides, because the cancellation carries no delay: a trip running late and cancelled between a stop's scheduled and its delayed departure keeps that stop's last prediction. A trip whose last status is `CANCELED` counts as not run even when it was cancelled partway through, or after its last stop.

### Excluding implausible values

(`2026.11.0`)

A report of a delay over 120 minutes (7,200 seconds), early or late, is dropped before deduplication. Such values come from a feed that has stopped updating and keeps resending the same prediction, not from real delays. A dropped report counts in no delay or on-time figure, so the stop's departure is decided by the reports before it. The share of dropped reports is one of the comparability conditions.

### Service day and time

(`2026.11.0`; after-midnight times `2026.11.1`)

- **Service day**: turns over at 04:00 JST, so a trip running from 23:40 to 00:30 falls in one service day.
- **Hours**: a departure scheduled at 24:00 or later counts under its service day's hour 24, 25, and so on. A time band such as morning or evening is taken from the clock time (24:30 is late night).

### Matching the timetable

(`2026.11.3`)

Each observation is matched with the timetable in force on its service day: the newest loaded timetable whose validity period (`feed_info`'s dates, or else `calendar`) covers that day.

- A timetable published ahead of its first day is never applied to the day before.
- A day no known timetable covers keeps the timetable its observations were ingested against.
- Figures read from the aggregates and figures scanned directly from the observations, such as a time-band filter, follow the same rule.
- The stamp names each timetable version in force over the period, with the number of days it covered.
  Where the timetable archive knows the version's contents, it adds a short content hash: the first eight characters of a fingerprint of the timetable's contents. Two versions with the same hash have the same contents, even under different names, and a version fetched again under its name with other contents shows another hash. A version the archive does not know shows none. Beside figures from the aggregates, the hash is shown only when the aggregates were built under that content: after a version is fetched again with other contents, it shows none until the aggregates are rebuilt.

### Where the observations came from

(`2026.11.2`)

Each day's observations are counted by the path they came from, archive or live, and the path holding most of them is that day's source. No delay or on-time figure changes under this rule.

### Rebuild record

When the method changes, every aggregate it changed is rebuilt over its whole history. Figures exported under an earlier version may therefore differ from today's for the same period.

When a rebuild rewrites the aggregate behind the operator-wide on-time rate and average delay, it records the following for each operator, shown on the admin board's run timeline:

- the operator-wide on-time rate and average delay, before and after, over the days the rebuild rewrote;
- the on-time definition they were counted under.

## Metrics

### On-time definition

| Name | On time when | Used |
|---|---|---|
| `standard_m1_p5` (industry standard) | no more than 1 minute early and no more than 5 minutes late | by default, on every screen and export |
| `legacy_60s` (legacy) | any early departure, and no more than 1 minute late | only when asked for |

- A request may also give its own early and late tolerances in seconds. A window equal to a named one is called by that name; any other is called `custom`.
- Choosing a definition changes no aggregate, so it is not a new method version. A figure is identified by its method version and its on-time definition together, and the stamp names both.

### Main metrics

An "observation" here is one departure: one trip, at one stop, on one day. It is not a count of trips.

| Metric | Computed as |
|---|---|
| On-time rate | departures within the on-time window ÷ observations |
| Average delay | sum of delays ÷ observations, early departures counted as negative |
| Median, 90th percentile | from the distribution of observed delays |
| Early-departure rate | the share of observations more than the definition's early tolerance early; under a definition that counts every early departure as on time, more than 1 minute early |
| Share more than 5 minutes late | the share of observations more than 5 minutes late, whatever the on-time definition |
| Service delivered | (planned trips − trips not run) ÷ planned trips, where planned trips are those the timetable in force plans for each day |
| On-time rate interval (used by the comparability conditions) | the 95% Wilson interval. Stops of one trip run late together, so the sample size is the number of trips, not of observations |

Service delivered is shown only for an operator whose feed has been confirmed to report cancellations and has reported at least one trip not run in the 90 days up to the period's last day (or within the period, when it is longer). Otherwise it is shown as "—" with the reason.

A route's service delivered is the same ratio on the route's own trips. A trip not run counts under the route its latest report names, so the routes add up to the operator. Its planned trips come from each day's saved split by route, or from the timetable loaded now. A period with a day saved before its split was saved has no route figure. The performance standards judge only a route with at least 100 planned trips and 100 observations.

**Headway metrics**: a route is high-frequency on a day when the median scheduled headway of the timetable in force that day is 12 minutes or less. Over a period, these metrics pool the days a route is high-frequency on:

- actual headways, reconstructed from consecutive departures at the same stop;
- Excess Waiting Time (EWT): actual mean wait − scheduled mean wait, the scheduled side taken from each pooled day's own timetable;
- the coefficient of variation of headways;
- the long-gap rate: the share of gaps longer than 1.75 times the scheduled headway of that day's timetable.

A headway median is taken the same way for scheduled and actual gaps: for an even count of gaps, it is the mean of the two middle gaps (from method `2026.11.9`).

Each day is judged by the timetable in force that day, not by the one loaded now (from method `2026.11.10`). A day whose timetable's scheduled headways were not kept is not judged by another timetable instead: it is left out, and the panel says how many days were. For a timetable kept before `2026.11.10`, only its median was kept, so its days are still classified and their gaps count, but EWT is not shown for a route whose period includes them, and the panel says how many such days there are.

**Dwell and running time**: each stop's actual arrival and departure are its scheduled times plus the reported delays. Dwell time is the actual departure minus the actual arrival at the same stop, counted only for a departure reported after the arrival, and running time is the actual arrival minus the previous stop's actual departure. Some feeds send the same value for arrival and departure delay, so how far this split can be trusted differs by operator.

### What is not observed

- A stop with no delay report counts neither as on time nor as late.
- A planned trip that never appears in the realtime data is not counted as not run. Service delivered subtracts from the plan only the trips the feed reported as not run.

## What is never computed

The Service neither computes nor publishes the following. Changing this rule starts with changing this document.

- **Figures per vehicle or per driver**:
  - No figure is made per vehicle or per driver.
  - The feeds' vehicle descriptors are not ingested. No database holds a column for a vehicle's identifier (`vehicle_id`), its licence plate (`license_plate`) or a driver's identifier (`driver_id`).
- **Passenger identity**:
  - No passenger identifier (`passenger_id`) or IC card number is held.
  - The only passenger weighting is a relative weight per route, entered by hand by an admin.
- **Fares**: nothing about fares (`fare`) is computed.

A test ([tests/unit/test_never_computed.py](../tests/unit/test_never_computed.py)) checks that no such column is in the schemas and that no vehicle descriptor survives into an ingested row.

**Raw archives**: the raw data collected is kept exactly as the operator published it.

- The realtime archive can carry vehicle descriptors the operator included. The Service does not ingest them.
- Positions are kept only to validate the predictions, for a bounded window (7 to 14 days of collection). They are not ingested into the app. Today they are kept as long as the realtime archive (1,825 days by default). A shorter retention for this validation archive is to be set.
- Any release of the archives as a public dataset strips the vehicle descriptors.

### The original fourteen gaps

Early in development, the perspectives a cross-operator delay analysis was missing were listed as fourteen items. Their current status follows. The `vehicle_id` column proposed then was never added, and is ruled out as above.

| Perspective | Status | Missing or notes |
|---|---|---|
| The on-time definition and early departures | Done | The industry-standard and legacy definitions, and any tolerance; the early-departure rate |
| High-frequency headways, EWT and long gaps | Done | Routes whose median scheduled headway is 12 minutes or less |
| The denominator: cancellations, missed trips and skipped stops | Partial | Cancellations and skips are counted. Service delivered per route serves the performance standards only; no screen lists it. A planned trip absent from the realtime data is not counted |
| Passenger weighting | Partial | Only hand-entered relative weights per route, used only by the weighted on-time rate on the agency comparison screen. No ridership import |
| Splitting running time from dwell time | Partial | The split exists. Whether a feed's arrival and departure delays are independent has not been checked feed by feed |
| External factors and comparison design | Partial | Only rain against dry days (one weather station per operator, daily, off by default). No road traffic, events or school calendar |
| Quality of the predictions | Partial | The stamp names the value basis. Validation against positions has just started. The monthly feed-quality scorecard and its A–F grade are on the admin pages and are not published |
| Stating the measurement point and definitions | Partial | "Download CSV" files, the council report, the delay record and the screens' "How this is calculated" block carry the definition stamp. CSVs a screen builds on the spot and chart images do not |
| Transfers and total travel time | Out of scope | Passengers are not the Service's primary users. Connection reliability at feeder hubs is considered separately |
| Policy KPIs and report formats | Done | The council report and the delay record. No Excel or PowerPoint formats yet |
| Data coverage | Partial | The Hiroshima Bus Association's thirteen operators and Aomori City Bus. 中国バス and 井笠バスカンパニー, on the association's list, are not ingested |
| Timetable slack and holding | Partial | Only an analysis on the Ask screen, against the current timetable. No aggregate and no route-page panel |
| Supply: planned trips, vehicle-km, timetable changes | Partial | Planned trips and vehicle-km per timetable version, and the change dates, exist. Only the current version is shown, with no comparison of which routes lost trips between versions. Vehicle-km delivered is approximated from the trip ratio |
| Contracts and incentives | Partial | Per-route standards (on-time rate, EWT, service delivered, vehicle-km delivered) with an achievement and bonus/deduction simulation. Service-delivered and vehicle-km standards are judged on the route's own service delivered |

## Comparability conditions

The agency comparison screen ranks only the operators whose data supports a comparison. It lists the rest unranked, each with the first condition it failed. The conditions are checked in this order, so one operator always gets the same reason:

| Order | Reason | Condition |
|---|---|---|
| 1 | No data | no observation in the period |
| 2 | Coverage unknown | planned and observed trips cannot be matched. Only operators ingested by a strategy whose trip IDs match the timetable's (`static_join`) can be compared |
| 3 | Coverage low | under 70% of planned trips observed |
| 4 | Interval wide | the on-time rate's 95% Wilson interval is more than 3.0 points either side |
| 5 | Implausible readings high | over 5% of reports dropped as implausible |

- Each threshold is inclusive: a figure exactly at it passes.
- Each check reads the figure rounded as it is printed, so a reader can confirm the verdict from the row itself.
- The on-time rate is counted under the default definition (`standard_m1_p5`).
- The council report states whether the operator is comparable under these conditions, with the figures behind the verdict.
- The thresholds' source is [pipeline/reports/comparability.py](../pipeline/reports/comparability.py). Changing a threshold changes no aggregate, so it is not a new method version. A change is announced by amending this section.

## Definition stamp

Figures shown or exported in the places listed below carry:

- the on-time definition's name, and its early and late tolerances;
- the threshold the early-departure rate counts past;
- the measurement scope: all stops, all observations;
- the deduplication rule, and the threshold for implausible values;
- the value basis: feed prediction;
- the method version, and the day the aggregates were built, or "live scan" for figures scanned directly from the observations;
- the timetable versions in force over the period, with their days and content hashes;
- where the observations came from: days from the archive and days from the live path;
- the feed's quality: the whole agency's observation coverage over the period (the coverage in the [Comparability conditions](#comparability-conditions)) and the share of implausible values dropped. A figure short of a comparability threshold says so in the words the agencies board uses; a figure that cannot be computed is never shown as 0 and gives its reason (no analyzed observations, trips that cannot be matched, no plan, or a plan that could not be read).

The stamp appears in the places below. The CSV preambles and the footnotes also give this document's URL.

- in the first-row preamble of each CSV fetched with "Download CSV";
- in the council report's footnotes, and on its print;
- in the delay record's footnotes;
- in the screens' "How this is calculated" block;
- on snapshot pages.

CSVs a screen builds on the spot from its export menu or "CSV" button (the route page's stops, the Reports screen's period summary, Live and Ask) and chart images carry neither the stamp nor the credit. Quote figures from a stamped export or a snapshot.

## Snapshots

A snapshot freezes the figures a screen showed, together with their stamp. Its link shows the same figures after any later rebuild. Figures quoted in council material are kept as snapshots.

- **Who creates them**: only admins. An admin's print of the council report takes one automatically and prints its link at the foot of the page.
- **Who reads them**: anyone holding the link, without signing in. The link's identifier is an unguessable random value, and only admins can list snapshots.
- **Compared with today**: a snapshot's page can compare its figures with today's, naming each side's method version and on-time definition.
- **Retention**: 2 years by default (provisional, awaiting the maintainer's confirmation). Nothing yet deletes a snapshot past that age; today a snapshot stays until an admin deletes it. Each creation and deletion is in the admin audit log.

The screens' behaviour is in [docs/features/snapshots.md](features/snapshots.md).

## Publication process

### Advance notice

At least **14 days** before the Service first makes an operator's figures public, the operator is sent:

- its figures;
- this document;
- a contact for reporting errors.

"Making public" means a page anyone can read without signing in, a published dataset, or an announcement by the maintainer. Opening the Service's own screens to anyone without signing in counts too.

- **Courtesy letter first**: before the first notice, a courtesy letter goes to the Hiroshima Bus Association explaining this process ([Courtesy letter to the association and operators](#courtesy-letter-to-the-association-and-operators)).
- **During the notice period**: the operator can point out errors in the figures or the method. Errors confirmed are corrected before publication.
- **Statement alongside**: the operator can send a short statement to be shown beside its figures.

The Service is not open to the public today. The public page and the public dataset are both in preparation. Until the first notice is sent, neither is published and the Service is not opened to the public.

### Reporting errors and takedown

- **Contact**: the Service's own address, [devyolouis@gmail.com](mailto:devyolouis@gmail.com). Bugs are also welcome on [GitHub Issues](https://github.com/TripAndCode/transit-app/issues), but Issues are public, so please leave personal information out.
- **Takedown**: a figure confirmed wrong is taken down or corrected **within 5 working days**.
- **Correction log**: each correction is recorded in this document's [Correction log](#correction-log). Once the public page exists, the log moves there. Each entry records:
  - the date;
  - the operator, figures and period affected;
  - what was wrong;
  - what was done;
  - the method version.

### Method changes

A method change does three things:

- it gets a new version number;
- what it changes is written down;
- every aggregate it changes is rebuilt over its whole history.

The changelog is at the end of this document, and each version's description is in [DELAY_ANALYSIS.md's "Method versions"](../DELAY_ANALYSIS.md#method-versions). How a rebuild moved each operator's on-time rate and average delay is recorded as in [Rebuild record](#rebuild-record).

## Governance

- **Maintainer**: the method and each publication are signed off by the operator of the Service, TripAndCode (an individual), as maintainer, until anyone else shares the role.
- **How to cite**: give the stamp's method version, build day and on-time definition, and the data source, for example "Delay Dashboard, method `2026.11.4`, aggregates built 2026-11-09, on-time definition `standard_m1_p5`". Add the snapshot link where there is one, so the figures can be checked later.
- **Reporting a problem**: through the contact in [Reporting errors and takedown](#reporting-errors-and-takedown).

### Personal data

- The Service's figures are about operators' services; it handles no passenger's personal data.
- Readers use it without signing in; only admins sign in.
- What personal data the Service handles, and how, is in the [privacy policy](../frontend/public/legal/privacy.en.md).

## Courtesy letter to the association and operators

Using CC0 data needs no one's legal consent. Even so, the Service avoids an operator finding its name and figures in public without having heard of them first. Before publication, a courtesy letter therefore goes to the Hiroshima Bus Association, explaining the Service's purpose and the process in this document.

The letter's text (in Japanese) is [templates/courtesy-letter.md](templates/courtesy-letter.md).

## Where this document is linked

This document is linked from:

- the app's footer (the welcome page's footer and the account page);
- the preamble of each CSV fetched with "Download CSV";
- the council report's and the delay record's footnotes, and their prints.

The public page will link to it once it exists.

## Correction log

No entries yet.

| Date | Affected | Error | Action | Method version |
|---|---|---|---|---|

## Changelog

What each version changed is described in [DELAY_ANALYSIS.md's "Method versions"](../DELAY_ANALYSIS.md#method-versions).

<!-- method-changelog:start -->
<!-- Written by poetry run python scripts/render_method_changelog.py from METHOD_VERSIONS. Do not edit by hand. -->

The current method is `2026.11.12`. Newest first, each version with the inputs it set.

| Version | Inputs set |
|---|---|
| `2026.11.12` | `feed_quality_monthly` 4 → 5 |
| `2026.11.11` | `feed_quality_monthly` 3 → 4 |
| `2026.11.10` | `agg_route_headway` 2 → 3, `agg_route_headway_daily` 3 → 4 |
| `2026.11.9` | `agg_route_headway` 1 → 2 |
| `2026.11.8` | `feed_quality_monthly` 2 → 3 |
| `2026.11.7` | `agg_service_delivered_daily` 2 → 3 |
| `2026.11.6` | `feed_quality_monthly` 1 → 2 |
| `2026.11.5` | `feed_quality_monthly` = 1 |
| `2026.11.4` | `agg_daily_trend` 2 → 3, `agg_hour_daily` 3 → 4, `agg_route_daily` 2 → 3, `agg_route_daily_dist` 2 → 3, `agg_route_daily_dwell_run` 2 → 3, `agg_route_headway_daily` 2 → 3, `agg_route_hour` 3 → 4, `agg_route_hour_dow` 3 → 4, `agg_route_stats` 2 → 3, `agg_route_stop_daily` 3 → 4, `agg_service_delivered_daily` 1 → 2, `agg_stop_daily` 3 → 4, `route_delay_counts` 3 → 4 |
| `2026.11.3` | `agg_daily_trend` 1 → 2, `agg_hour_daily` 2 → 3, `agg_route_daily` 1 → 2, `agg_route_daily_dist` 1 → 2, `agg_route_daily_dwell_run` 1 → 2, `agg_route_headway_daily` 1 → 2, `agg_route_hour` 2 → 3, `agg_route_hour_dow` 2 → 3, `agg_route_stats` 1 → 2, `agg_route_stop_daily` 2 → 3, `agg_schedule_revision_daily` 1 → 2, `agg_stop_daily` 2 → 3, `agg_stop_routes` 1 → 2, `route_delay_counts` 2 → 3, `stop_route_days` 1 → 2 |
| `2026.11.2` | `agg_feed_health` 1 → 2 |
| `2026.11.1` | `agg_hour_daily` 1 → 2, `agg_route_hour` 1 → 2, `agg_route_hour_dow` 1 → 2, `agg_route_stop_daily` 1 → 2, `agg_stop_daily` 1 → 2, `route_delay_counts` 1 → 2 |
| `2026.11.0` | `MAX_PLAUSIBLE_DELAY_SEC` = 7200, `agg_daily_trend` = 1, `agg_feed_health` = 1, `agg_hour_daily` = 1, `agg_route_daily` = 1, `agg_route_daily_dist` = 1, `agg_route_daily_dwell_run` = 1, `agg_route_headway` = 1, `agg_route_headway_daily` = 1, `agg_route_hour` = 1, `agg_route_hour_dow` = 1, `agg_route_stats` = 1, `agg_route_stop_daily` = 1, `agg_schedule_revision_daily` = 1, `agg_service_delivered_daily` = 1, `agg_stop_daily` = 1, `agg_stop_routes` = 1, `route_delay_counts` = 1, `stop_route_days` = 1 |

<!-- method-changelog:end -->
