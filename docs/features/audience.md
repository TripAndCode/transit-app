# Audience and job

Who the app is for, the one job it does for them, and what that means for each
screen. A screen, a default or a new feature that serves no one here needs a reason
before it is built.

## The primary user and their job

**交通政策担当者**: transport-policy staff at a city or prefecture who prepare the
quarterly material for the **地域公共交通協議会** (regional public-transport council).

Their job: *did the buses we subsidise or contract run as planned over the period,
where and when not, and what goes into the council paper?*

They work at a desk. They need figures they can defend in a meeting: each number with
its definition, its period, its data source and how much data it rests on. They
deliver on paper or as files: print, PDF and CSV.

## What the app is

An **independent service-quality audit instrument**. It derives its numbers from the
operators' own published GTFS and GTFS-Realtime feeds, without the operators' internal
systems. That independence is the point: the council can check a service without
relying on the operator's own report of it.

It can also grow into a **national GTFS-RT observatory**: one deployment that watches
many public Japanese feeds for feed quality and reliability. That breadth needs no
relationship with each operator.

Funding fits that model: a grant- or municipality-funded instrument, or open-source
civic tech. It is not SaaS sold to operators.

## Secondary readers

- **協議会 members and academics** reading the material. They read the printed or
  shared output, so every exported figure carries its definition and period.
- **Visitors comparing operators or feeds** at a national scale. Comparisons need a
  fairness gate: coverage, sample size and comparable service.

## Not the primary user

- **Dispatchers and 運行管理者.** Their bus-location vendor's console already serves
  real-time operations. Live stays as a way to look at today, not as a dispatch tool.
- **Passengers.** Journey planners already show them real-time predictions. The delay
  record stays a reference document, not a passenger product.
- **Demand-responsive transport planning.** It needs ridership, cost and subsidy data
  that the app does not hold.
- **ML delay forecasting.** No user above needs a day-ahead forecast. The model
  evaluation page is an admin page, and further ML work is parked.

## Consequences for the product

- **Desk first.** Print, PDF and definition-stamped CSV come before phone polish and
  animated map styles.
- **One period everywhere.** The screen a user lands on, its headline figures and the
  scope sentence above them describe the same period.
- **Numbers carry their basis.** Each figure states its definition, its period and the
  data behind it. A figure resting on thin data says so rather than ranking anything.
- **The deliverable is close.** The council report is the persona's output, so it is
  never buried behind analyst screens.

## Screens

The default screen after choosing an agency is **Pulse**, because the persona's first
question is how the period went.

| Screen | Its question for the persona |
|---|---|
| **Pulse** (概況) | How did the period go, and what changed? |
| **Routes** (路線) | Which routes account for it? |
| **Time** (時間) | At which days and hours? |
| **Why** (要因) | Is it dwell at stops or running between them? |
| **Compare** (比較) | Weekday against weekend, and operator against operator. |
| **Live** (ライブ) | What is running today? A check, not a dispatch console. |
| **Reports** (レポート) | The material: the period summary and the council report, plus the delay record as a reference. |
| **Ask** (質問) | Ready-made questions answered as tables and charts. |

Admins also see the admin pages and **Model evaluation**.

## Knowing whether it works

The cheapest signal is to count, monthly against production, the distinct users who
made a signed-in request (by session or by API key) over the last 30 days:

```sql
SELECT count(DISTINCT user_id), max(day) FROM user_activity_daily WHERE day > now() - interval '30 days';
```
