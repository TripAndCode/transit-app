# Welcome page (`/welcome`)

Public landing page, rendered outside `<App />` and outside `RequireAuth`
(`frontend/src/main.tsx`) so a signed-out visitor can reach it. Its hero
has one CTA, the way in this visitor has:

- **Signed in:** "Open the dashboard" (`/`).
- **Signed out, and `GET /api/config` says `login_required` is off:** "View
  the dashboard (no sign-in needed)" (`/`), with a note that signing in keeps
  saved views and Ask investigations with the account. The note's "Sign in"
  links to `/login`, and the note is left out when no provider is configured.
- **Signed out otherwise** (sign-in required, or the config not loaded yet):
  "Sign in" (`/login`). `/` would only bounce such a visitor back here.

Signed-in users still reach the page.

## The hero plays once

The animated map (`LiveMapHero`) plays its script once and stops on its last
frame, the settled live-operations view. A control in the hero's corner
pauses it while it plays and replays it from `REPLAY_START`
(`heroMapTimeline.ts`) once it has ended. Under `prefers-reduced-motion` the
settled frame is drawn once and no control is shown.

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

## The scroll narrative is real charts on fixture data, not a live demo

Below the hero, `frontend/src/pages/landing/ScrollNarrative.tsx` renders
three sections — each a heading/body pair beside a real chart component:
the route-delay `StopChart`, the day-over-day `DailyChart`, and Ask's
`StopEvidenceChart`. Each fades into view (`useRevealOnScroll`) behind
`prefers-reduced-motion: no-preference`. None of them fetches: every figure
is static example data from `frontend/src/pages/landing/previewData.ts`, so
the page shows the product without exposing any real data to a signed-out
visitor.

All three show one sample route, route 12 of the hero's fictional city, with
the hero's stop names (`landing.hero_map.stops.*`) in the reader's language:
a stop carries the same figure in the stop chart and in the Ask evidence,
which lists the route's worst few stops ranked, as the real hotspot answer
does. Each chart fits its figure at any width: the stop chart is drawn at the
figure's measured width (`StopChart`'s `width`), so nothing scrolls sideways.

## Key files

| File | Role |
|---|---|
| `frontend/src/pages/LandingPage.tsx` | Hero: headline and the sign-in or dashboard CTA |
| `frontend/src/pages/LandingPage.css` | Hero and CTA styling |
| `frontend/src/pages/landing/LiveMapHero.tsx` | Animated backdrop behind the hero: the operations map with trip dots and the app's right-hand panel, on a fictional city whose rail lines are scenery only (every trip and figure is a bus route's). Scene data in `heroMapScene.ts`, script in `heroMapTimeline.ts`, map layers in `heroMapDraw.ts`, panel in `heroPanelDraw.ts`, driven by `useHeroMapAnimation.ts` |
| `frontend/src/pages/landing/ScrollNarrative.tsx` | Post-hero scroll narrative on fixture data |
| `frontend/src/pages/landing/previewData.ts` | Static fixture data for the narrative's charts |
| `frontend/src/components/RequireAuth.tsx` | Sends signed-out visitors here or to `/login` when sign-in is required |
| `frontend/src/api/authExpiry.ts` | Refetches session and config on an auth-required 401 |

## i18n

Hero strings live under `landing.hero.*`
(`frontend/src/i18n/locales/{ja,en}.json`): `title_now` and `title_where`
(the headline's two lines, rendered as separate spans so it never breaks
mid-phrase), `subtitle`, and `open_dashboard` (the signed-in CTA). The
sign-in CTA reuses `common.login`; the rail link is `nav.about`. Text drawn
on the hero canvas lives under `landing.hero_map.*`, its playback control
under `landing.hero_map.playback_control.*`; the narrative's heading/body
pairs under `landing.narrative.{route,trend,ask}.{title,body}`, and its
sample caption under `landing.narrative.sample_caption`. The guest CTA and
its note are `landing.hero.view_dashboard` and `landing.hero.login_note`;
the tagline above the headline is `header.app_tagline`.

## How to verify manually

1. With SSO configured and `login_required` on, sign out and open `/` —
   expect `/welcome` with a single "Sign in" button.
2. Open a deep link such as `/agencies/1/routes?routes=12&sort=on_time` —
   expect `/login?next=…`; sign in and expect to land on that exact URL.
3. Sign in, then choose "About this app" in the rail — expect `/welcome`
   with "Open the dashboard" in place of "Sign in". With `login_required`
   off and signed out, expect "View the dashboard (no sign-in needed)".
4. Sign out from the account page — expect `/welcome`.
5. Automated coverage: `frontend/src/pages/LandingPage.test.tsx`,
   `frontend/src/pages/AccountPage.test.tsx`,
   `frontend/src/pages/LoginPage.test.tsx`,
   `frontend/src/components/Sidebar.test.tsx`,
   `frontend/src/components/RequireAuth.test.tsx`,
   `frontend/src/api/authExpiry.test.ts`.
