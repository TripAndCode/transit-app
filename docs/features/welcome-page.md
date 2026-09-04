# Welcome page (`/welcome`)

Pre-authentication marketing/landing page rendered outside `<App />` (no
Header, sidebar, or guest-prompt strip — see `frontend/src/main.tsx`). It
exists purely to make a first impression before login; it is not on the
path any signed-in or guest user actually needs to pass through, since the
root route (`/`, `frontend/src/components/OnboardingGate.tsx`) already
renders the real dashboard with no auth guard.

## Two entry paths, one real destination

`frontend/src/pages/LandingPage.tsx` renders a hero with two links, styled
at deliberately different visual weights:

- **Primary — "Sign in"** (`landing-hero__cta`, a filled button): links to
  `/login`.
- **Secondary — "Continue as a guest"** (`landing-hero__guest-cta`, a plain
  underlined text link placed directly beneath the primary button): links
  to `/`.

Both ultimately land on the same real, already-guest-accessible dashboard
(`OnboardingGate` → the signed-in app shell, `frontend/src/App.tsx`, which
renders `<GuestPrompt />` rather than redirecting an anonymous visitor
anywhere). The secondary link exists because the app supported guest
browsing before this page did — the page's job is to describe what the app
already does, not to gate access on its own. It is intentionally a lower-
emphasis text link rather than a second same-weight button: two competing
full-weight CTAs would dilute the primary action and add choice friction
for a visitor who hasn't decided yet, whereas a single dominant default
(sign in) plus one clearly secondary, still-discoverable alternative (guest)
matches how most product landing pages present an optional lower-commitment
path.

## The dashboard preview is a mock, not a live demo

Below the hero, `frontend/src/pages/landing/DashboardPreview.tsx` renders a
shell structurally matching the real `Sidebar.tsx` + `App.tsx` (collapsible
sidebar, the real nav set, full-bleed Map tab, functional controls) and
auto-advances through the real tabs (Overview → Map → Analysis → Network →
Live, `AUTO_ADVANCE_ORDER` in `DashboardPreview.tsx`) on its own, cycling
indefinitely so a visitor who never touches it still sees the whole
sequence. "Ask" is deliberately excluded from the auto-advance cycle — it is
presented as a CTA a visitor opts into, not a peer tab in the cycle, mirroring
how `PreviewSidebar` itself treats it.

Every panel in the preview (`PreviewOverviewPanel`, `PreviewMapPanel`,
`PreviewAnalysisPanel`, `PreviewNetworkPanel`, `PreviewLivePanel`,
`PreviewAskPanel`) reads from static, hardcoded fixture data
(`frontend/src/pages/landing/previewData.ts`), never a live API call. This
is what stands in for a produced demo video — it shows the product moving
without needing a maintained recording — and it keeps the preview itself
free of any cost or data-exposure question entirely independently of the
guest-access policy described above: the preview would render identically
even if guest access to the real app were removed tomorrow.

## Anonymous Ask usage carries a daily quota; the rest of the dashboard does not

Every read-only tab — Overview, Map, Analysis, Agencies, Live — behaves
identically for a guest and a signed-in user: same data, same precomputed
aggregates, no throttling beyond the ordinary API-wide rate limit (see
below). The one exception is the Ask tab's Stage-3 LLM path
(`api/middleware/ratelimit.py`), because it is the one guest-accessible
feature that invokes a paid LLM call per request; every other guest-visible
tab only reads precomputed `agg_*` tables.

- Default caps: 5 Stage-3 LLM calls per anonymous session per day
  (`ASK_ANON_DAILY_LIMIT`), with a looser 20/day per-IP backstop
  (`ASK_ANON_IP_DAILY_LIMIT`) that exists only to blunt wholesale abuse from
  one source cycling through many anon-session cookies, not to further
  restrict the common case of several distinct legitimate visitors sharing
  one IP (e.g. office wifi). Both read live from the environment
  (`ask_anon_daily_limit()` / `ask_anon_ip_daily_limit()` in
  `api/middleware/ratelimit.py`), not import-frozen.
- A single-digit daily cap on anonymous AI usage before requiring an
  account is an established pattern, not a bespoke restriction invented for
  this app — see
  [Perplexity's free-tier daily limit on anonymous search](https://www.perplexity.ai/hub/faq/what-is-perplexity-pro)
  for a comparable published example.
- Exhausting the quota does not error or block the rest of the app: the
  Ask tab shows a calm sign-in nudge (the quota-exceeded response,
  `ASK_ANON_QUOTA_EXCEEDED_CODE`) and every non-Ask tab, plus Ask's own
  deterministic template dispatch (the primary landing-card/chip path,
  which never calls an LLM), keeps working.
- This is a pragmatic per-session/IP cap, not a sophisticated abuse-
  detection system — a deliberate, documented scope limit. It does not
  fingerprint devices, rate-limit by behavioral heuristics, or attempt to
  survive an attacker rotating both session cookies and IPs; it only needs
  to keep a casual anonymous visitor from running up a meaningful LLM bill,
  which a flat daily counter accomplishes without added complexity.
- The kill switch (`ASK_ANON_QUOTA_ENABLED`, default on) disables the quota
  entirely when set falsy — the anon caller then behaves exactly as it did
  before this quota existed. The quota applies only to anonymous callers;
  once a user signs in, this quota no longer applies to them (whatever the
  authenticated per-key rate-limit tier is takes over instead — see the
  comparison below).

## Guest vs. authenticated: what actually differs

| Capability | Guest | Signed-in |
|---|---|---|
| Overview / Map / Analysis / Agencies / Live tabs | Full read access, identical data | Identical |
| Ask tab — deterministic template dispatch (cards/chips) | Works, no LLM involved | Identical |
| Ask tab — Stage-3 LLM (free-text / novel questions) | Works, subject to the daily quota above | Works, no quota |
| Ask conversation persistence | Browser `localStorage` only (`frontend/src/api/conversationsAnon.ts`) | Server-side, durable across devices |
| First login after guest Ask use | N/A | One-time anon→server migration of any local conversations fires automatically (`frontend/src/tabs/AskTab.tsx`'s `authed` effect, guarded by a ref so it fires at most once) |
| Saved filter presets | Cannot save (`presets.login_to_save_tooltip` — `frontend/src/components/PresetMenu.tsx` disables the save action with this tooltip) | Can save and reuse |
| Admin console (`/admin/users`) | Unreachable — `frontend/src/components/RequireAdmin.tsx` redirects an unauthenticated caller to `/login` and a non-admin signed-in caller to `/` | Reachable only for `role=admin` |
| Generic API rate-limit tier | 60/minute (`FREE_LIMIT`, `api/middleware/ratelimit.py`) | 600/minute with a pro-tier API key (`PRO_LIMIT`) |

In short: the differences between guest and signed-in are entirely about
saving, persisting, and administering — never about what data can be
viewed. For how a signed-in account actually becomes an admin
(`ADMIN_EMAILS` promotes on first login; subsequent admins are promoted via
the console), see `README.md`'s "First admin" section rather than
duplicating that flow here.

## Key files

| File | Role |
|---|---|
| `frontend/src/pages/LandingPage.tsx` | Hero: headline, sign-in CTA, secondary guest link |
| `frontend/src/pages/LandingPage.css` | Hero/hero-CTA/guest-link styling |
| `frontend/src/pages/landing/CityMapHero.tsx` | Animated background scene behind the hero text |
| `frontend/src/pages/landing/DashboardPreview.tsx` | Auto-advancing mock dashboard shell below the hero |
| `frontend/src/pages/landing/previewData.ts` | Static fixture data consumed by every preview panel |
| `frontend/src/components/OnboardingGate.tsx` | What `/` actually renders — the real, guest-accessible dashboard entry |
| `frontend/src/components/GuestPrompt.tsx` | Persistent, dismissible guest-login nudge shown inside the real app shell |
| `api/middleware/ratelimit.py` | `FREE_LIMIT`/`PRO_LIMIT` generic tiers; anonymous Ask daily quota (`ask_anon_daily_limit`, `check_and_consume_anon_quota`) |
| `frontend/src/api/conversationsAnon.ts` | localStorage-backed anon Ask conversation store |
| `frontend/src/components/RequireAdmin.tsx` | Admin-only route guard |

## i18n

Hero strings live under `landing.hero.*`
(`frontend/src/i18n/locales/{ja,en}.json`): `title`, `subtitle`,
`guest_cta`. The primary sign-in CTA reuses the shared `common.login` key
rather than a `landing`-scoped one.

## How to verify manually

1. `make frontend-dev` (or `make serve` for single-origin), navigate to
   `/welcome`.
2. Confirm the hero shows exactly one filled button ("Sign in") and one
   plain text link beneath it ("Continue as a guest").
3. Click "Sign in" — expect navigation to `/login`, unchanged from before
   this link existed.
4. Return to `/welcome`, click "Continue as a guest" — expect navigation to
   `/`, landing on the real dashboard (Overview tab by default) with no
   login prompt blocking access, and the guest-login nudge (`GuestPrompt`)
   visible as a dismissible banner rather than a hard gate.
5. Automated coverage: `frontend/src/pages/LandingPage.test.tsx` asserts
   both links and their `href`s independently.
