# Welcome page (`/welcome`)

Public landing page, rendered outside `<App />` and outside `RequireAuth`
(`frontend/src/main.tsx`) so a signed-out visitor can reach it. Its hero
offers one way in, "Sign in", which links to `/login`. A signed-in visitor
who opens `/welcome` is sent to `/`.

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

## The scroll narrative is real charts on fixture data, not a live demo

Below the hero, `frontend/src/pages/landing/ScrollNarrative.tsx` renders
three sections — each a heading/body pair beside a real chart component:
the route-delay `StopChart`, the day-over-day `DailyChart`, and Ask's
`StopEvidenceChart`. Each fades into view (`useRevealOnScroll`) behind
`prefers-reduced-motion: no-preference`. None of them fetches: every figure
is static example data from `frontend/src/pages/landing/previewData.ts`, so
the page shows the product without exposing any real data to a signed-out
visitor.

## Key files

| File | Role |
|---|---|
| `frontend/src/pages/LandingPage.tsx` | Hero: headline and the sign-in CTA |
| `frontend/src/pages/LandingPage.css` | Hero and CTA styling |
| `frontend/src/pages/landing/LiveMapHero.tsx` | Animated backdrop behind the hero: the operations map with trip dots and the app's right-hand panel, on a fictional city. Scene data in `heroMapScene.ts`, script in `heroMapTimeline.ts`, map layers in `heroMapDraw.ts`, panel in `heroPanelDraw.ts`, driven by `useHeroMapAnimation.ts` |
| `frontend/src/pages/landing/ScrollNarrative.tsx` | Post-hero scroll narrative on fixture data |
| `frontend/src/pages/landing/previewData.ts` | Static fixture data for the narrative's charts |
| `frontend/src/components/RequireAuth.tsx` | Sends signed-out visitors here or to `/login` when sign-in is required |
| `frontend/src/api/authExpiry.ts` | Refetches session and config on an auth-required 401 |

## i18n

Hero strings live under `landing.hero.*`
(`frontend/src/i18n/locales/{ja,en}.json`): `title_now` and `title_where`
(the headline's two lines, rendered as separate spans so it never breaks
mid-phrase) and `subtitle`. The sign-in CTA reuses `common.login`. Text drawn
on the hero canvas lives under `landing.hero_map.*`; the narrative's
heading/body pairs under `landing.narrative.{route,trend,ask}.{title,body}`.

## How to verify manually

1. With SSO configured and `login_required` on, sign out and open `/` —
   expect `/welcome` with a single "Sign in" button.
2. Open a deep link such as `/agencies/1/analysis/ranking?route=12` —
   expect `/login?next=…`; sign in and expect to land on that exact URL.
3. Sign in, then open `/welcome` — expect `/`.
4. Automated coverage: `frontend/src/pages/LandingPage.test.tsx`,
   `frontend/src/components/RequireAuth.test.tsx`,
   `frontend/src/api/authExpiry.test.ts`.
