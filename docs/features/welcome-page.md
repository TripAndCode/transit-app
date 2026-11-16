# Welcome page (`/welcome`)

Public landing page, rendered outside `<App />` (`frontend/src/main.tsx`) so
it owns the full viewport. One CTA decision
(`frontend/src/pages/landing/useCtaKind.ts`) drives both the header button and
the hero's primary CTA. Both go to the dashboard (`/`), which every visitor
reaches; only the wording follows the session:

- **Signed in:** "Open the dashboard".
- **Anyone else, a session still loading included:** "View the dashboard (no
  sign-in needed)". Once the visitor is known to be signed out, a note under
  the hero says that signing in keeps saved views and notebook investigations
  with the account. The note's "Sign in" links to `/login`, and the note is
  left out when no provider is configured.

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
section's own CTA (`BusTownTeaser.tsx`): it goes straight to Bus Town for
the visitor's last-chosen agency (`readLastAgency`), or to `/` to choose one
when none is stored yet, signed in or not (`busTownLink.ts`). Every other link on the page — the header nav, the three
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

- **Signing out** (`AccountPage`) reloads onto `/welcome`.
- **The rail's Other section** (and the mobile More sheet, which shares it)
  links "About this app" to `/welcome`.
- **The login card's wordmark** links to `/welcome`.

## Who gets sent here

No guard sends a visitor here; only signing out and the links above do. The
app shell renders for every visitor, signed in or not, and no guard in front
of it waits on the session or the config: a shared
link opens the same view for anyone. A signed-out visitor at `/` gets the
same landing decision as anyone (`OnboardingGate`); its agency picker says
that no sign-in is needed and links back to this page.

Two guards remain, and both send a signed-out visitor to
`/login?next=<path+query+fragment>`, so signing in returns them to the page
they asked for:

- `RequireAdmin` (`frontend/src/components/RequireAdmin.tsx`) on `/admin/*`;
  a signed-in non-admin goes to `/` instead.
- The account page, `/me`.

A request the API refuses with `401 {"detail": "auth required"}` (a session
that expired in an open tab) refetches the session
(`frontend/src/api/authExpiry.ts`), so those guards decide again on a fresh
value.

The session probe itself, `GET /api/me`, answers a signed-out caller with
`200` and a `null` body, so no page logs a refused request. `fetchMe`
(`frontend/src/api/auth.ts`) reads `null` (or a `401`) as signed out. Every
other `/api/me/*` route, and `DELETE /api/me`, refuses a signed-out caller.

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
| `frontend/src/api/authExpiry.ts` | Refetches the session on an auth-required 401 |

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

1. With SSO configured, sign out and open `/welcome` — expect "View the
   dashboard (no sign-in needed)" in the header and in the hero, and the
   sign-in note under the hero.
2. Signed out, open a deep link such as
   `/agencies/1/routes?routes=12&sort=on_time` — expect that view at that
   exact URL, with no detour through `/login` or `/welcome`.
3. Sign in, then choose "About this app" in the rail — expect `/welcome`
   with "Open the dashboard" and no sign-in note.
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
   otherwise, signed in or not.
8. Automated coverage: `frontend/src/pages/LandingPage.test.tsx`,
   `frontend/src/pages/LandingPage.css.test.ts`,
   `frontend/src/pages/landing/busTownLink.test.ts`,
   `frontend/src/pages/landing/severityStyle.test.ts`,
   `frontend/src/pages/AccountPage.test.tsx`,
   `frontend/src/pages/LoginPage.test.tsx`,
   `frontend/src/components/Sidebar.test.tsx`,
   `frontend/src/components/OnboardingGate.test.tsx`,
   `frontend/src/main.readerAccess.test.tsx`,
   `frontend/src/api/authExpiry.test.ts`.
