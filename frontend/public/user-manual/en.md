# Delay Dashboard — User Manual

This manual is a walkthrough for people using the Delay Dashboard for the first time.
The **Analysis** tab has the most features and gets the most "I don't get it" feedback,
so that section is covered in extra detail, step by step.

Sample data: screenshots use real data from Hiroshima Electric Railway (agency 8).
Other agencies (Aomori City Bus / Hiroshima Bus / Hiroshima Kotsu) work exactly the same way.

---

## Table of contents

1. [Choosing an agency (landing screen)](#1-choosing-an-agency-landing-screen)
2. [Using the screens in general (the filter bar)](#2-using-the-screens-in-general-the-filter-bar)
3. [Overview tab — "what's happening right now"](#3-overview-tab--whats-happening-right-now)
4. [Map tab — "where it's happening"](#4-map-tab--where-its-happening)
5. [Analysis tab — "when and why delays happen" [most important]](#5-analysis-tab--when-and-why-delays-happen-most-important)
6. [Agencies tab — "how you compare to others"](#6-agencies-tab--how-you-compare-to-others)
7. [Latest observations tab — "the buses right now"](#7-latest-observations-tab--the-buses-right-now)
8. [Ask tab — ask in a conversation](#8-ask-tab--ask-in-a-conversation)
9. [Other (about the PROTOTYPE section)](#9-other-about-the-prototype-section)

---

## 1. Choosing an agency (landing screen)

When you open the app, you first see a screen for choosing an agency (bus/rail operator).

![Agency selection screen](./01-agency-select.png)

Click the card for the agency you want to view to enter that agency's dashboard.
Once you've chosen an agency, the app automatically reopens the screen you last had open
(to switch agencies, click the logo in the top-left corner, or reselect from the
agency-name dropdown ▾ at the top of the sidebar).

---

## 2. Using the screens in general (the filter bar)

Nearly every screen except the Ask tab has this "filter bar" at the top.

- **Date range** (e.g. "Last 30 days")
- **Detailed filters** for day-of-week / time-of-day / service type ("Filters ▾")

Changing the range or conditions here filters every number, chart, and table on that
page to match. **The setting is kept when you switch tabs** (it's also kept in the URL).

If you just want to see the overall picture, leaving the range at "Last 30 days" is fine.
Use this filter bar when you want to dig into a specific route or time window.

> Note: a yellow "Data is out of date" banner sometimes appears at the top of the screen.
> This just means data collection is running a little behind — it isn't an app failure.
> You can dismiss it with the close button (×).

---

## 3. Overview tab — "what's happening right now"

This is the first tab shown after signing in. **It's the place to check, at a glance, how
delayed things are overall today.**

![Overview tab](./02-overview.png)

Key things to look at:

- **NETWORK AVG DELAY**: the average delay in minutes over the chosen period, with the
  change from last week also shown.
- **ROUTES DELAYED**: the share of all routes that are actually running late.
- **FEED STATUS**: whether data is arriving all the way up to now.
- **ROUTES TO CHECK NOW**: a list of routes with especially bad delays (5+ minutes, etc).
  Click a route name to see its detailed status.

If you just want the big picture, this tab alone is enough.

---

## 4. Map tab — "where it's happening"

The map shows average delay at each stop/station as a colored circle (bubble).

![Map tab](./03-map.png)

- **Color**: represents delay size (green = little to no delay → orange/red = large delay).
- **Circle size**: bigger circles mean bigger delays.
- **Number inside a circle**: the number of observation points (stops) grouped at that spot.
- The "Legend" panel in the top-left explains the color scale and symbols.
- Checking "Show single-sample stops" also shows stops with little data (lower confidence).

Use this when you want to understand geographically where delays are concentrated.

---

## 5. Analysis tab — "when and why delays happen" [most important]

This tab has the most features and is the part people find hardest to follow. It's
explained step by step below.

### 5-1. Screen layout

When you open the Analysis tab with nothing selected yet, it looks like this.

![Analysis tab (initial state)](./04-analysis-landing.png)

The screen is split into three main areas.

| Area | Location | Role |
|---|---|---|
| ① Report list | Left side | Pick the "angle" (report type) you want to look at |
| ② Main area | Center | The chart/table for the selected report shows up here |
| ③ "Worth a look" panel | Right side | Notices about "interesting changes" the app found automatically |

**In short, the flow is: "① click one report in the left menu" → "② the chart or table
appears in the center."** At first the center just says "Select a report" — that isn't
broken, it just means nothing has been picked yet.

### 5-2. Types of reports (① left menu)

The left menu lists the following reports. Matched to "what you want to know":

- **Delay ranking**: to see which routes are the most delayed, ranked.
- **On-time ranking**: the opposite — to see which routes run most on time.
- **On-time rate**: to see, per route, the percentage of arrivals that were on time.
- **≥5-min delays**: to see only especially large delays (5+ minutes).
- **Trend**: to see how delay changes day by day, and how it differs by weekday/time.
- **Compare ranking**: to compare routes side by side.
- **Weekend pattern / Weekday pattern**: to see whether weekday and weekend trends differ.
- **Route forecast**: to see a forecast of upcoming delay trends.

If you're not sure where to start, look at **Delay ranking** (which routes are bad) first,
then **Trend** (when they're bad).

### 5-3. Stuck? Click the "?" hint icon

Clicking the small round "ⓘ" (hint) icon to the right of the word "Reports" opens a
**popup explaining how to read each report**. This built-in usage guide is the first
place to check whenever you're unsure.

![Hint popup](./06-analysis-hint-popover.png)

The popup explains things like:

- Ranking tables are for finding "routes that are consistently late / consistently on time."
- Trend is for seeing whether things are getting better or worse — trending up means worsening.
- The time-of-day heatmap is for comparing which times of day tend to be busiest.
- Weekday/weekend patterns are for spotting day-of-week habits.
- CSV export lets you download the table data to Excel or similar.

**A similar "?" icon can also appear near the heading of individual report screens.**
If a chart's meaning isn't clear, look for this icon first.

### 5-4. Trying an actual report: Delay ranking

Clicking "Delay ranking" in the left menu lists routes ordered from most to least delayed.

![Delay ranking screen](./05-analysis-ranking.png)

What the columns mean:

- **Avg**: average delay (minutes).
- **Median**: the middle value — a "typical" delay that isn't skewed by a few extreme days.
- **p90**: the delay in the worst 10% of cases (a rough gauge of "how bad it gets on a bad day").
- **Samples**: the number of observations behind that row (fewer samples = lower confidence).

The "⬇ CSV" button in the top-right downloads this table as-is.

### 5-5. Trend report — seeing when delays happen

Clicking "Trend" in the left menu shows a screen like this.

![Trend report screen](./07-analysis-trend.png)

- The colored grid at the top (day of week × time of day) is a heatmap of **when delays
  tend to happen**, using color intensity. Darker (closer to red) means bigger delays.
  In the example capture, it's immediately clear that "Friday evening" is worst.
- The line chart below it shows the day-by-day trend of average delay. Trending up means
  it's getting worse; trending down means it's improving.

### 5-6. On-time rate report

![On-time rate report screen](./08-analysis-ontime.png)

Routes are ranked by the percentage of arrivals that were on time. Closer to 100% is better.

### 5-7. The "Worth a look" panel on the right

A small panel is always shown on the right side of the Analysis tab. This is a relatively
new feature: **an "alerts" area where the app automatically spots changes in the data**
and tells you about them — no manual digging required.

![Worth a look panel (initial state)](./04-analysis-landing.png)

For example, it might show something like "Route 3968526772's delay pattern shifted
partway through this week," along with the route to watch and the size of the change
(e.g. +4.2 min).

Clicking "View" inside the panel automatically jumps to a Trend screen focused on that
route and period.

![Result after clicking View](./09-analysis-insight-view.png)

Notice that the filter bar at the top of the screen now has the period and route-name
chips (tags) set automatically. This is the same filter bar described in section 2, being
reused here inside the Analysis tab. To go back to the unfiltered state, click "Clear
all" in the filter bar.

The arrow (▶) in the panel's top-left corner also lets you collapse it. **There isn't
always a notice** — the panel can be empty when there's nothing notable to flag.

### 5-8. Summary: how to use the Analysis tab

1. Click one report (angle) you want to see from the left menu.
2. If a term is unclear, click the "ⓘ" hint icon and read the explanation.
3. If the "Worth a look" panel on the right has a notice, start there — the app is telling
   you about an important change.
4. To focus on a specific route or period, filter using the bar at the top.

---

## 6. Agencies tab — "how you compare to others"

The sidebar shows this as "Agencies (How you compare to others)" — it's actually a
**network-wide screen that compares your agency against every other agency**.

![Agencies tab](./10-network.png)

Agencies are ranked from smallest to largest average delay, with a "YOU" badge on your
own agency. Use this to check how you're doing relative to everyone else.

---

## 7. Latest observations tab — "the buses right now"

The sidebar labels this "Latest observations," but it's really a **near-real-time view of
current operations**.

![Latest observations tab](./11-live.png)

- **Watch**: routes where today's delay is significantly higher than normal are listed
  here. "vs normal +9 min" means "running 9 minutes later than usual."
- **Normal**: the number of routes behaving as usual.
- **No baseline**: routes that don't yet have enough history to establish a "normal" baseline.

When "Auto-refresh" (top right) is checked, the screen updates automatically every 30
seconds. Use this screen to check "is anything happening right now."

---

## 8. Ask tab — ask in a conversation

Instead of hunting for the right chart yourself like on the other tabs, the Ask tab lets
you **pick what you want to know in a chat-like flow and get an answer (a table or chart)
right away**.

![Ask tab landing screen](./12-ask-landing.png)

How to use it:

1. Pick the kind of question you want from the center of the screen, or from the
   "question picker" at the bottom (e.g. 🏆 Top-N delays, 🎯 On-time rate ranking,
   📈 Route delay trend, and so on).
2. Adjust conditions like "Top: 5" or "Service: All" if needed, then press "Run."

Once you ask, the result appears on screen like this.

![Ask answer screen](./13-ask-answer.png)

- The question you picked is shown as a bubble on the right (e.g. "Top 5 routes (All)").
- Below it, the answer — a table, in this case the top-5 delay ranking — is shown.
- Below the table, buttons for **follow-up questions** appear, such as "**Why this
  pattern?**" and "**Is the sample size reliable?**." Click one to continue the conversation.
- **There's also a free-text box below that.** For a question that isn't covered by the
  provided buttons (e.g. "Are any routes worse than last week?"), type it in as plain text
  and press "Send" to get an answer grounded in the result currently on screen.

![Follow-up free-text input](./14-ask-followup-freetext.png)

> Note: this free-text box is meant specifically for **digging further into the result
> already on screen** (to build a brand-new ranking from scratch, use the quick-question
> shortcuts or the question picker below instead). If an answer can't be produced, a
> message explaining why appears — too long, high traffic, a connection error, etc. —
> follow what it says: shorten the question, wait a moment, or try again.

- Below that, **quick-question shortcuts** (icon buttons: 🏆🎯📈⚖️🚏) let you start a new question.
- The sidebar on the left keeps a history of past questions, grouped by conversation.
  Click "＋ New conversation" to start a new one.

If you're not sure which report to look at, it's often faster to just pick what you want
to know on the Ask tab rather than hunting through the Analysis tab.

---

## 9. Other (about the PROTOTYPE section)

At the bottom of the sidebar, under a "PROTOTYPE" heading, there are links like
"First-time login screen," "Feed-stale state," and "No-data state." These are
**internal/developer previews** for checking edge cases (unusual screen states) and
aren't used in normal day-to-day work. Clicking them by accident won't break anything,
but you can generally ignore this section.

---

That covers the full walkthrough. For the **Analysis** tab in particular, remembering the
three steps — **"① pick a report from the left menu → ② check the ⓘ hint for how to read
it → ③ check the notice in the Worth a look panel on the right"** — should keep you from
getting lost.
