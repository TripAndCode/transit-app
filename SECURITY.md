# Security Policy

## Reporting a vulnerability

Please report suspected security vulnerabilities privately through GitHub's
security advisory mechanism: open a
[new draft security advisory](https://github.com/TripAndCode/transit-app/security/advisories/new)
for this repository. Do not open a public issue or pull request for a
suspected vulnerability.

Include as much detail as you can: affected component, reproduction steps,
and potential impact.

## Scope

This covers the FastAPI backend (API), the React single-page application
(frontend), and the GTFS-RT collector. Infrastructure outside this repository
(hosting, managed databases, third-party services) is out of scope for
reports here.

## What not to do

Do not run tests, scans, or load against production feeds or the production
deployment. Use a local or throwaway environment for any proof-of-concept.

## Response expectations

We aim to acknowledge new advisories within a few business days. Timelines
for a fix depend on severity and complexity; we'll keep the advisory thread
updated as we work on it.
