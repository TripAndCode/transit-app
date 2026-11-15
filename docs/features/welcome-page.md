# Welcome page (`/welcome`)

Public landing page, rendered outside `<App />` and outside `RequireAuth`
(`frontend/src/main.tsx`) so a signed-out visitor can reach it. One CTA
decision (`frontend/src/pages/landing/useCtaKind.ts`) drives both the header
button and the hero's primary CTA, the way in this visitor has:

- **Signed in:** "Open the dashboard" (`/`).
- **Signed out, and `GET /api/config` says `login_required` is off:** "View
  the dashboard (no sign-in needed)" (`/`), with a note that signing in keeps
  saved views and Ask investigations with the account. The note's "Sign in"
  links to `/login`, and the note is left out when no provider is configured.
- **Signed out otherwise** (sign-in required, or the config not loaded yet):
  "Sign in" (`/login`). `/` would only bounce such a visitor back here.

Below 720px the header hides its copy of the CTA, since the hero's sits in the
first screen.

Signed-in users still reach the page.

## Every figure is a labelled sample, never a live read

The page walks a visitor through what the product shows — a nationwide
real-time observatory, a council-ready report, graded feed quality, a Bus
Town day replayed stop by stop, and the calculation method behind every
number — using only static fixture data from
`frontend/src/pages/landing/welcomeSample.ts`. Nothing on `/welcome` calls the
API for its figures, so a signed-out visitor is never shown a real agency's
numbers. Every figure, table, chart and the map carries a visible "Sample"
label (`SampleTag`, or the council report's own `landing.report.badge`), so
it reads as illustrative rather than live.

The one link that leaves the page for a real destination is the Bus Town
section's own CTA (`BusTownTeaser.tsx`): a visitor who would reach the
signed-in app (a session, or a guest-enabled deployment) goes straight to
Bus Town for their last-chosen agency (`readLastAgency`), or to `/` to choose
one when none is stored yet; anyone else goes to sign-in first
(`busTownLink.ts`). Every other link on the page — the header nav, the three
"who it's for" cards, each section's own in-page link — points at an anchor
(`#councils`, `#observatory`, `#bus-town`, `#method`) on this same page, never
a destination the product doesn't have.

## Motion pauses from one control

The hero's observatory stage, the Bus Town mini map, and the method section's
flow indicator each loop a small CSS animation (a breathing city dot, buses
riding a motion path, a pulse moving through the three pipeline steps),
declared only inside `@media (prefers-reduced-motion: no-preference)` so a
visitor who has asked for less motion never receives them. A single visible
toggle in the header (`PauseToggle.tsx`) freezes every one of them at once,
regardless of that media query, via `.landing-shell.is-paused *, ::before,
::after { animation-play-state: paused }` — the one override that must apply
whether or not reduced motion is already in effect.

## Ways back to it

- **Signing out** (`AccountPage`) reloads onto `/welcome`, whether or not
  sign-in is required.
- **The rail's Other section** (and the mobile More sheet, which shares it)
  links "About this app" to `/welcome`.
- **The login card's wordmark** links to `/welcome`.

## Who gets sent here

`RequireAuth` (`frontend/src/components/RequireAuth.tsx`) wraps the app
shell. It reads `login_required` from `GET /api/config`:

- **Off or absent** (SSO not configured, the kill switch off, or a backend
  without the login gate): the app renders for everyone, signed in or not.
- **On, and the visitor is signed out:** `/` goes to `/welcome`, and any
  other app URL goes to `/login?next=<path+query+fragment>`, so a shared link
  returns to the same view after sign-in.
- **A probe that fails before producing any value** shows an error with a
  retry instead of guessing.

A request the API refuses with `401 {"detail": "auth required"}` (a session
that expired in an open tab) refetches the session and the config
(`frontend/src/api/authExpiry.ts`), so the guard decides again on fresh
values.

The session probe itself, `GET /api/me`, answers a signed-out caller with
`200` and a `null` body, whether or not sign-in is required: the login gate
lets that one read through, so no page logs a refused request. `fetchMe`
(`frontend/src/api/auth.ts`) reads `null` (or a `401`) as signed out. Every
other `/api/me/*` route, and `DELETE /api/me`, still refuses a signed-out
caller.

A signed-out visitor who opens `/me` while sign-in is not required goes to
`/login?next=/me`, so signing in returns them to their account page.

## Key files

| File | Role |
|---|---|
| `frontend/src/pages/LandingPage.tsx` | Page shell: header, section order, footer |
| `frontend/src/pages/LandingPage.css` | All of the page's own styling, reusing `global.css` tokens |
| `frontend/src/pages/landing/useCtaKind.ts` | The one CTA decision, shared by the header and hero |
| `frontend/src/pages/landing/CtaLink.tsx` | Renders that CTA, used in both places |
| `frontend/src/pages/landing/WelcomeHero.tsx` | Headline, lead, CTAs, and the observatory stage |
| `frontend/src/pages/landing/ObservatoryStage.tsx` | The hero's dark dotted map of Japan |
| `frontend/src/pages/landing/StatusBar.tsx` | The fixture figures under the hero (feeds, buses tracked, on-time rate) |
| `frontend/src/pages/landing/AudienceCards.tsx` | "Who it's for": three in-page links into the sections below |
| `frontend/src/pages/landing/FeedTable.tsx` | Sample A–F feed-quality grading table |
| `frontend/src/pages/landing/CouncilSample.tsx` | Sample council quarterly report figure |
| `frontend/src/pages/landing/MethodSteps.tsx` | Collect/compare/publish pipeline and the "what we don't hide" honesty list |
| `frontend/src/pages/landing/BusTownTeaser.tsx` | Bus Town mini map and its one real CTA (`busTownLink.ts`) |
| `frontend/src/pages/landing/PauseToggle.tsx` | The page's one motion control |
| `frontend/src/pages/landing/welcomeSample.ts` | Every static fixture value the page draws |
| `frontend/src/pages/landing/severityStyle.ts` | Maps a fixture band to the app's shared delay-severity ramp |
| `frontend/src/components/RequireAuth.tsx` | Sends signed-out visitors here or to `/login` when sign-in is required |
| `frontend/src/api/authExpiry.ts` | Refetches session and config on an auth-required 401 |

## i18n

Every visible string on the page lives under `landing.*`
(`frontend/src/i18n/locales/{ja,en}.json`), one sub-key per section
(`landing.hero.*`, `landing.stage.*`, `landing.status.*`, `landing.doors.*`,
`landing.observatory.*`, `landing.councils.*`, `landing.report.*`,
`landing.method.*`, `landing.bustown.*`, `landing.pause_toggle.*`,
`landing.footer.*`), plus `landing.nav.*` for the header's in-page links and
`landing.sample_tag` for the shared "Sample data" label. The page reuses
three strings that already exist for the rest of the app rather than
duplicating them: `header.app_title`/`header.app_tagline` for the brand, and
`nav.bus-town` for the Bus Town nav link and CTA.

## How to verify manually

1. With SSO configured and `login_required` on, sign out and open `/` —
   expect `/welcome` with a single "Sign in" button, in the header and in
   the hero.
2. Open a deep link such as `/agencies/1/routes?routes=12&sort=on_time` —
   expect `/login?next=…`; sign in and expect to land on that exact URL.
3. Sign in, then choose "About this app" in the rail — expect `/welcome`
   with "Open the dashboard" in place of "Sign in". With `login_required`
   off and signed out, expect "View the dashboard (no sign-in needed)".
4. Sign out from the account page — expect `/welcome`.
5. Click the header's pause toggle — expect every looping animation on the
   page (the stage dots, the Bus Town markers, the method flow pulse) to
   freeze in place, and the toggle to flip back to playing on a second
   click.
6. At a 360px-wide viewport, in both languages — expect no sideways scroll,
   the pause toggle as an icon beside the brand, and the CTA only in the
   hero.
7. Click the Bus Town section's button — expect it to land on that agency's
   Bus Town tab when a last-chosen agency is stored, or on `/` to choose one
   otherwise, or on `/login` when the deployment requires sign-in.
8. Automated coverage: `frontend/src/pages/LandingPage.test.tsx`,
   `frontend/src/pages/LandingPage.css.test.ts`,
   `frontend/src/pages/landing/busTownLink.test.ts`,
   `frontend/src/pages/landing/severityStyle.test.ts`,
   `frontend/src/pages/AccountPage.test.tsx`,
   `frontend/src/pages/LoginPage.test.tsx`,
   `frontend/src/components/Sidebar.test.tsx`,
   `frontend/src/components/RequireAuth.test.tsx`,
   `frontend/src/api/authExpiry.test.ts`.
