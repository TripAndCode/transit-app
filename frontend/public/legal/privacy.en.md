# Privacy Policy

Last updated: November 17, 2026

Delay Dashboard (“the Service”) is run by TripAndCode, an individual. This page explains what information about you the Service handles and how. The Japanese version of this policy is the authoritative one.

Every screen of the Service can be used without signing in. Only the Service's administrators sign in.

## Operator

- Name: TripAndCode (run by an individual)
- Full name and address: provided without delay on request.
- Contact: [devyolouis@gmail.com](mailto:devyolouis@gmail.com), an address used only for the Service. Bugs and requests are also welcome on [GitHub Issues](https://github.com/TripAndCode/transit-app/issues), but Issues are public, so please don't include personal information there.

## What we collect and why

### Everyone who uses the Service

| Information | When | Why |
|---|---|---|
| Questions, without a record of who asked them | When you ask a question in the Notebook or through the API | To answer them, and to improve answers |
| Administrator password sign-in attempts (time, IP address, browser type, and for a failed attempt a one-way fingerprint of the username typed) | When anyone tries to sign in with the administrator password, including attempts that fail | To detect and investigate attempts to break into administrator accounts |

Please don't put personal information in a question. An administrator password sign-in never records the password typed.

We also handle the following:

- **Daily request counts:** we count requests and errors per day for each screen or API route and transit operator. These counts hold nothing that shows whose request it was, such as an IP address or a cookie.
- **Rate limiting:** to limit excessive access, the server counts requests per IP address in memory for about a minute. The address is not stored.
- **Server logs:** for each request we log the time, the URL requested (without the query after “?”), the outcome and how long it took. The logs contain no IP address or browser type. When an administrator is signed in, they also contain the administrator's account number.
- **Sign-in by someone who isn't an administrator:** when someone who isn't an administrator tries to sign in with Google or GitHub, the sign-in is refused. We receive their email address from the provider only to check whether they are an administrator, and don't store it. We record only the time, the sign-in method and the reason it was refused.

### Administrators

| Information | When | Why |
|---|---|---|
| Email address, display name, avatar image URL | When an administrator signs in with Google or GitHub | To identify the administrator and show their account |
| Sign-in and key-action history (time, sign-in method or kind of action, IP address, browser type) | When an administrator signs in or out, or takes an action that manages accounts or transit operators | To investigate misuse and keep the Service secure |
| Sessions (IP address, browser type, last used) | While an administrator is signed in | To keep them signed in, and to let them sign out other devices |
| Daily usage counts (requests and errors per screen or API route, and the transit operator being viewed) | While an administrator is signed in | To understand usage and manage load |
| Investigations recorded in the Notebook | When the administrator saves them | To provide that feature |
| The admin action log (time, kind and target of the action, the values before and after, IP address) | When an administrator takes an admin action | To keep a record of admin actions and keep the Service secure |

When an administrator signs in, investigations that browser kept in the Notebook move into their account.

### What stays in your browser

Display settings, saved views, saved analyses, My routes, the last filter you used, and investigations recorded in the Notebook without signing in are kept in your browser. They are not stored on our servers.

### Cookies

The Service shows no ads and uses no analytics tools. Cookies are used only for administrator sign-in: one keeps an administrator signed in (up to 30 days), and two temporary ones are used only during sign-in (up to 10 minutes). If you don't try to sign in, no cookies are set.

## How long we keep it

| Information | Period |
|---|---|
| Questions | 90 days after a question was last asked |
| Sign-in and key-action history, including failed administrator password sign-ins | 25 months |
| An administrator's account | Until the account is deleted |
| Investigations an administrator recorded in the Notebook | Until the administrator deletes them or the account is deleted |
| Sessions | Up to 30 days |
| An administrator's daily usage counts | 25 months |
| The admin action log | 400 days |

Records past their period are deleted automatically every day. Daily request counts that hold nothing showing whose request it was are kept without a set period.

## Sending information to service providers

Except where the law requires it, we don't give your personal information to third parties without your consent. To run the Service, we have the following service providers handle it on our behalf.

- **Servers and database:** the Service is not currently offered to the public. Before it is, this page will name the provider and the country it is based in.
- **Google LLC and GitHub, Inc.:** administrator sign-in (only the one the administrator chooses).
- **Google LLC (Gemini API) and OpenAI, L.L.C.:** when an LLM answers a question, including a question about a result in the Notebook, we send the question and the aggregate data needed to answer it.

The sign-in and LLM providers are all based in the United States. You can read about the United States' personal-information protection system in the Personal Information Protection Commission's survey of foreign systems (「外国における個人情報の保護に関する制度等の調査」).

## What your browser loads directly

Your browser loads map images, the glyphs for map labels, and the page fonts directly from their providers. As with any web page, those providers receive your IP address and browser type.

- **Map images:** the Geospatial Information Authority of Japan (GSI Tiles) or the OpenStreetMap Foundation (OpenStreetMap tiles), depending on the map style you choose.
- **Map label glyphs:** MapLibre's demo server (demotiles.maplibre.org).
- **Fonts:** Google LLC (Google Fonts).

## What you can do

- **Delete what your browser keeps:** clearing this site's data in your browser removes everything listed under “What stays in your browser”.
- **Questions:** because we don't record who asked a question, we can't find or delete one person's questions.
- **Deleting an administrator account:** the operator deletes administrator accounts. Deleting one removes its email address, display name and avatar image, and deletes its sessions, its link to Google or GitHub, and the investigations it recorded in the Notebook. Its sign-in and key-action history and the admin action log remain until their periods above pass.
- **Other requests:** requests for disclosure, correction, suspension of use and the like go to the contact above.

## How we keep it safe

- Connections are encrypted with HTTPS.
- Administrator sign-in is handled by Google and GitHub. The only password the Service holds is the administrator password, stored as a one-way hash.
- Only the operator and the administrators the operator appoints can see personal information.
- The country where the servers and database are located will be stated with their provider, under “Sending information to service providers”.

## Changes to this policy

When this policy changes, we'll announce it on this page.
