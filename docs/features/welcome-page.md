# Welcome page (`/welcome`)

Pre-authentication marketing/landing page rendered outside `<App />` (no
Header, sidebar, or guest-prompt strip — see `frontend/src/main.tsx`). It
exists to make a first impression before login. A signed-in visitor, or an
anonymous visitor who has already passed through it once, is never routed
here and can reach the root route (`/`,
`frontend/src/components/OnboardingGate.tsx`) directly, which renders the
real dashboard with no auth guard — but a genuinely first-time anonymous
visitor is redirected here from `/` (see "First-time redirect from `/`"
below), so this page is the first thing a new anonymous visitor actually
sees.

## First-time redirect from `/`

`OnboardingGate` (`frontend/src/components/OnboardingGate.tsx`) gates every
visit to `/` on a dedicated localStorage flag,
`frontend/src/api/welcomeSeen.ts` (`readWelcomeSeen`/`writeWelcomeSeen`,
key `transit.welcomeSeen`) — deliberately separate from
`frontend/src/api/lastAgency.ts`'s stored agency choice, since "Continue as
a guest" lands on `/` without ever picking an agency, so gating on
`lastAgency` alone would loop straight back to `/welcome` on the very next
visit.

- A visitor with an active auth session (`useSession()` from
  `frontend/src/api/auth.ts`) is never redirected, regardless of the
  flag's state. Only a confirmed-anonymous session (`useSession()` settled
  with no error and no data) counts as anonymous for this gate — a session
  check that errors instead (a non-401 `/api/me` failure, surfaced after
  react-query exhausts its retry) falls through to the dashboard/picker
  rather than risk misrouting a signed-in visitor whose probe merely
  hiccupped.
- An anonymous visitor with the flag unset is redirected to `/welcome`
  instead of ever seeing the dashboard or the multi-agency picker overlay.
- A visitor whose flag can't be reliably read or written falls through to
  the dashboard/picker instead of redirecting. This covers a store whose
  `getItem` throws outright, and separately, a store whose `setItem` throws
  or silently fails to persist (e.g. quota already exhausted by other keys)
  while `getItem` keeps working — `writeWelcomeSeen()` reads the value back
  after writing it and remembers, in memory for the rest of the tab session,
  that the write didn't actually land, so `readWelcomeSeen()` reports
  `"unavailable"` rather than `"unseen"` on the next mount. Without that
  memory, a write-only failure would look identical to a genuine first visit
  on every subsequent mount and redirect to `/welcome` forever, including
  right after "Continue as a guest".
- The flag is set unconditionally the moment `OnboardingGate` mounts —
  whichever branch that particular render takes (the redirect to
  `/welcome`, the dashboard, or the picker) — so a browser is only ever
  sent to `/welcome` once. This covers every path that reaches `/`: the
  welcome page's own "Continue as a guest" link, a `/login` success
  redirect, and a direct deep link straight to `/`.

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

## The scroll narrative is real charts on fixture data, not a live demo

Below the hero, `frontend/src/pages/landing/ScrollNarrative.tsx` renders three
sections — each a heading/body pair beside a real, working chart component:
the route-delay `StopChart` (the same component Route analysis uses), the
day-over-day `DailyChart` (the same component Analysis's trend report uses),
and Ask's `StopEvidenceChart` (the same evidence view a real Ask answer
renders). Each section fades and rises into place as it scrolls into view
(`useRevealOnScroll`), gated behind `prefers-reduced-motion: no-preference`
in `ScrollNarrative.css` so a reduced-motion visitor sees every section fully
visible immediately instead of animating in.

None of the three components ever fetches — every figure is illustrative,
static example data from `frontend/src/pages/landing/previewData.ts` (an
invented route whose delay compounds stop after stop against a calmer
week-earlier comparison, a couple of rough days against a calmer trailing
average, and one Ask evidence point deliberately missing so the fixture
exercises the same no-data path real data hits). This is what stands in for a
produced demo video — it shows the product's real chart components moving
without needing a maintained recording — and it keeps the narrative free of
any cost or data-exposure question entirely independently of the guest-access
policy described above: it would render identically even if guest access to
the real app were removed tomorrow.

## A guest reaches no LLM at all; the rest of the dashboard is fully open

Every read-only tab — Overview, Map, Analysis, Agencies, Live — behaves
identically for a guest and a signed-in user: same data, same precomputed
aggregates, no throttling beyond the ordinary API-wide rate limit (see
below). Those tabs only read precomputed `agg_*` tables, so guest access
costs nothing per request.

The LLM-backed surfaces are the exception, and they are closed to guests
outright rather than budgeted: reaching an LLM requires a signed-in caller
an admin has approved (`users.llm_approved` — see
`docs/features/ask-tab.md`'s "Who may reach the Stage-3 LLM"). That keeps
paid calls off the guest path entirely, which is why no anonymous LLM
allowance exists to tune.

Guests are not shut out of the Ask tab itself. Its deterministic stages —
the regex rules and the embedding nearest-neighbour lookup behind the
landing cards and chips, which is the primary path — answer without an LLM
and stay open to everyone. Only a free-text question that would need
Stage 3 degrades, and it degrades honestly (a `200` explaining the answer
isn't available) rather than erroring.

## Guest vs. authenticated: what actually differs

| Capability | Guest | Signed-in |
|---|---|---|
| Overview / Map / Analysis / Agencies / Live tabs | Full read access, identical data | Identical |
| Ask tab — deterministic template dispatch (cards/chips) | Works, no LLM involved | Identical |
| Ask tab — Stage-3 LLM (free-text / novel questions) | Unreachable; degrades to an honest "not available" answer | Works once an admin sets `users.llm_approved`; same degradation until then |
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
| `frontend/src/pages/landing/LiveMapHero.tsx` | Animated live-map backdrop behind the hero text (fictional city, sample data); geometry, timeline and renderer live in `heroMapScene.ts`, `heroMapTimeline.ts`, `heroMapDraw.ts`, driven by `useHeroMapAnimation.ts` |
| `frontend/src/pages/landing/ScrollNarrative.tsx` | Post-hero scroll narrative: three real charts on fixture data |
| `frontend/src/pages/landing/useRevealOnScroll.ts` | Fade/rise-in-view hook backing the narrative's scroll reveal |
| `frontend/src/pages/landing/previewData.ts` | Static fixture data consumed by the scroll narrative's charts |
| `frontend/src/components/OnboardingGate.tsx` | What `/` actually renders — the real, guest-accessible dashboard entry; redirects a genuinely first-time anonymous visitor to `/welcome` |
| `frontend/src/api/welcomeSeen.ts` | localStorage-backed "has this browser passed the welcome step" flag consulted by `OnboardingGate` |
| `frontend/src/components/GuestPrompt.tsx` | Persistent, dismissible guest-login nudge shown inside the real app shell |
| `api/middleware/ratelimit.py` | `FREE_LIMIT`/`PRO_LIMIT` generic per-minute tiers |
| `frontend/src/api/conversationsAnon.ts` | localStorage-backed anon Ask conversation store |
| `frontend/src/components/RequireAdmin.tsx` | Admin-only route guard |

## i18n

Hero strings live under `landing.hero.*`
(`frontend/src/i18n/locales/{ja,en}.json`): `title_now` and `title_where`
(the headline's two lines, rendered as separate spans so it never breaks
mid-phrase), `subtitle`, `guest_cta`. The primary sign-in CTA reuses the
shared `common.login` key rather than a `landing`-scoped one. Text drawn on
the hero canvas (station and district names, captions, callout, legend,
HUD) lives under `landing.hero_map.*`. The scroll narrative's three
heading/body pairs live under `landing.narrative.{route,trend,ask}.{title,body}`
in the same locale files.

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
   both links and their `href`s independently;
   `frontend/src/pages/landing/ScrollNarrative.test.tsx` and
   `useRevealOnScroll.test.ts` cover the post-hero narrative and its scroll
   reveal.
6. First-time redirect: clear the browser's localStorage (or open a private
   window), log out if signed in, then navigate straight to `/` — expect an
   immediate redirect to `/welcome`. Click "Continue as a guest" and confirm
   it lands on the dashboard; navigate to `/` again in the same
   browser/tab and confirm it no longer redirects. Automated coverage:
   `frontend/src/components/OnboardingGate.test.tsx`'s "welcome redirect"
   suite.
