# Deploy to Railway (tag-gated push-to-deploy)

Lowest-effort path: Railway runs your **existing Docker images** — the app
(`Dockerfile`, with the SPA baked in) and the custom PostGIS+pgvector DB
(`db/Dockerfile`) — on private networking, with free TLS and a managed
domain. No box to harden, no Caddy, no SSH.

**Point every Railway service at the `production` branch, not `main`.**
This repo runs an autonomous VPS loop (see CLAUDE.md "Autonomous VPS loop")
that continuously opens PRs against `main`, and — since 2026-08-28 — both the
loop and interactive sessions may squash-merge a PR themselves once the
required `/review-branch` pass is clean and it's mergeable/clean, with no
separate human go-ahead required. If Railway watched `main` directly, every
one of those merges — reviewed but not yet soak-tested in a real deploy —
would auto-deploy and run `preDeployCommand` migrations immediately, with no
remaining checkpoint before production traffic sees it. Instead, `main` is
just the integration branch; `production` only ever moves when a human
deliberately promotes a reviewed, merged commit to it (see step 6) — that
promotion is now the *only* human checkpoint left before a deploy, and that
is what actually triggers a Railway deploy. The `production` branch already
exists in this repo for exactly this purpose.

Cost: ~$10–18/mo usage-based, in exchange for zero server ops and git-push
deploys — pick this if you'd rather not manage a Linux box yourself.

> **Why not a managed Postgres add-on?** Migration `0001` does
> `CREATE EXTENSION postgis / vector`, and `0012` adds `pg_trgm`. Not every
> managed PG offering supports all three. Running our own `db/Dockerfile` on
> Railway keeps app + DB on one usage bill and sidesteps any "does the
> platform have my extension" question entirely.

---

## 0. Prereqs

- A [Railway](https://railway.com) account (Hobby plan, $5/mo minimum, covers this).
- The repo pushed to GitHub.
- `railway.json` (in repo root) — pins the app service to the Dockerfile
  builder, the `/health` healthcheck, and runs `migrate up` as a
  **pre-deploy command** before every release. Railway's equivalent of
  Fly's `release_command`; `migrate up` is idempotent (tracked in
  `schema_migrations`), so re-running is a no-op.
- Optional CLI: `npm i -g @railway/cli` then `railway login`. The steps
  below are dashboard-driven; the CLI is only needed for `railway run`
  one-offs (migrations from your Mac, ad-hoc psql).

---

## 1. Create the project + the database service

Two services in one project. Do the **DB first** so its private hostname
exists when you wire the app.

1. New Project → **Empty Project**.
2. **+ New → GitHub Repo → your `transit-app`**. Name this service `db`.
3. `db` service → **Settings → Source → Branch**: change it from the
   default (`main`) to **`production`**. Do this for every service in this
   guide (`db`, `app`, `ingest`) — see the note above on why.
4. `db` service → **Settings → Build**:
   - Root Directory: `db`
   - Builder: **Dockerfile**
   - Dockerfile Path: `Dockerfile`

   The root directory is what makes the build context `db/`, matching every
   other caller. The Dockerfile copies its extension gate from alongside
   itself, so a context rooted at the repository instead cannot resolve it and
   the build fails on the COPY.
5. `db` → **Variables** (these feed the `postgres` image `db/Dockerfile` builds on):
   ```
   POSTGRES_USER=transit
   POSTGRES_PASSWORD=<openssl rand -hex 24>
   POSTGRES_DB=transit
   PGDATA=/var/lib/postgresql/data/pgdata
   ```
   `PGDATA` must be a subdirectory of the volume mount below, not the mount
   path itself: Railway's block-storage volumes arrive pre-populated with a
   `lost+found` directory, and `initdb` refuses to treat a non-empty
   directory as fresh — every first boot crash-loops on `initdb: error:
   directory "/var/lib/postgresql/data" exists but is not empty` without
   this.
6. `db` → **Settings → Volumes → + Volume**, mount path:
   ```
   /var/lib/postgresql/data
   ```
   **Without this the database is wiped on every redeploy.** This is the
   one stateful piece of the whole deploy.
7. Deploy `db`. Wait until it's running.

> Postgres is reachable **only** on the private network. Don't add a public
> TCP proxy to it — the app talks to it internally, same as the compose
> `db` service. Reference it from other services as `${{db.RAILWAY_PRIVATE_DOMAIN}}`
> (Railway's own variable-interpolation syntax) rather than a hardcoded
> `db.railway.internal` name — the exact hostname is Railway's implementation
> detail, and the reference stays correct if that ever changes; step 1b's
> `clickhouse` service uses the same convention. The daily ingest + backup
> jobs (steps 4 and 7) also run **inside** the project, so nothing external
> ever needs to reach the DB.

---

## 1b. Create the ClickHouse service

Raw GTFS-RT `updates` lives in ClickHouse, not Postgres (see CLAUDE.md ▸
Architecture pointers) — `db` above only covers the OLTP/aggregate/pgvector
side. Unlike `db`, ClickHouse needs no custom extensions, so this service
deploys straight from the official image: no Dockerfile, no repo checkout.

1. **+ New → Empty Service**. Name it `clickhouse`.
2. `clickhouse` → **Settings → Source → Source Image**: set it to
   `clickhouse/clickhouse-server:26.8` — the same tag `compose.yml` and the
   Makefile's `ch-test` pin locally, so dev and production run identical
   ClickHouse behavior.
3. `clickhouse` → **Variables** (the official image's own bootstrap vars,
   read once on first start):
   ```
   CLICKHOUSE_USER=transit
   CLICKHOUSE_PASSWORD=<openssl rand -hex 24>
   CLICKHOUSE_DB=transit
   ```
4. `clickhouse` → **Settings → Volumes → + Volume**, mount path:
   ```
   /var/lib/clickhouse
   ```
   Same reason as `db`'s volume: without it, every redeploy starts from an
   empty `updates` table.
5. Deploy `clickhouse`. Wait until it's running.

> Like `db`, ClickHouse is reachable **only** on the private network — no
> TCP proxy, no public port. Reference it from other services as
> `${{clickhouse.RAILWAY_PRIVATE_DOMAIN}}`, Railway's own variable-
> interpolation syntax, rather than hardcoding a `.railway.internal` name:
> the exact hostname is Railway's implementation detail, and the reference
> stays correct if that ever changes. `app`'s pre-deploy command (step 2.3)
> applies the ClickHouse schema the same way `migrate up` applies Postgres's,
> so there's no separate manual bootstrap step here beyond this service
> existing and being reachable.

---

## 2. Create the app service

1. **+ New → GitHub Repo → the same `transit-app`**. Name it `app`.
2. `app` → **Settings → Source → Branch**: change it from the default
   (`main`) to **`production`** (see the note at the top of this guide).
3. Railway auto-detects `railway.json` → Dockerfile builder + `/health`
   healthcheck + the pre-deploy command (applies Postgres migrations, then
   the ClickHouse schema from step 1b). Nothing to configure.
4. `app` → **Variables**:
   ```
   DATABASE_URL=postgresql://transit:<the POSTGRES_PASSWORD from step 1.5>@${{db.RAILWAY_PRIVATE_DOMAIN}}:5432/transit
   CLICKHOUSE_HOST=${{clickhouse.RAILWAY_PRIVATE_DOMAIN}}
   CLICKHOUSE_PORT=8123
   CLICKHOUSE_USER=transit
   CLICKHOUSE_PASSWORD=<the CLICKHOUSE_PASSWORD from step 1b.3>
   CLICKHOUSE_DATABASE=transit
   CLICKHOUSE_SECURE=false                 # private network, no TLS needed internally
   GEMINI_API_KEY=...
   CRON_SECRET=<openssl rand -hex 32>      # save this — it must match the GH secret (step 4)
   CHAT_PROVIDERS=gemini                    # add ",openai" (and set OPENAI_API_KEY) for a paid fallback rung
   CORS_ORIGINS=                           # leave EMPTY — SPA + API are same-origin
   ```
   - `PORT` is injected by Railway automatically; the Dockerfile's
     `--port ${PORT:-8000}` honours it. Don't set it yourself.
   - SSO is optional. To enable it, also set `SESSION_SIGNING_KEY`,
     `GOOGLE_/GITHUB_CLIENT_ID/SECRET`, `ADMIN_EMAILS`, and
     `PUBLIC_BASE_URL=https://<your-railway-domain>` (a *partial* OAuth set
     is rejected at startup). Add the
     `https://<domain>/api/auth/{google,github}/callback` redirect URIs at
     the provider. See README ▸ Authentication.
   - `DEFAULT_ADMIN_USERNAME`/`DEFAULT_ADMIN_PASSWORD` are an optional
     break-glass local-admin login, independent of SSO — see README ▸
     Configuration. Never set `DEFAULT_ADMIN_USERNAME` to a real SSO user's
     email; rotate the password by editing the Railway variable and
     redeploying.
   - `OPS_STATUS_REPO` (see README ▸ Configuration) is not needed here
     unless this service's own checkout path differs from the collectors'
     default — leaving it unset just means the admin board's `vps_loop`/
     `github` collector tiles read "unknown".
5. `app` → **Settings → Networking → Generate Domain**. Railway issues
   `https://<something>.up.railway.app` with TLS. Copy it — that's
   `APP_BASE_URL` for the cron and `PUBLIC_BASE_URL` for SSO.
6. First deploy: promote `main` to `production` now (see the "Updates"
   section below) to actually trigger it. The pre-deploy `migrate up` runs
   first; watch **Deploy Logs** for its `Applied N migration(s).` line (see
   `db/migrations/README.md` for how migrations are numbered — the count
   grows over time, so no specific range is quoted here), then the uvicorn
   boot line.

Smoke test:

```bash
curl -fsS https://<your>.up.railway.app/health      # → 200
```

Open the domain in a browser — SPA loads. Tabs are empty until data lands
(next step), and Ask works once `GEMINI_API_KEY`/`OPENAI_API_KEY` is valid.

> **Image spec — the Ask-tab embedder is not in this image.** `sentence-
> transformers` (and its transitive `torch`/`transformers`/`scikit-learn`/
> `scipy`) live in poetry's optional `embeddings` group, which the
> Dockerfile's `--only main` deliberately skips — several GB, unused until a
> RAG index actually exists. `pipeline.query.embeddings.Embedder`'s import is
> wrapped in try/except; every caller already falls through to the LLM-only
> path when it's unavailable, so Ask still works (Stages 1 and 3), just
> without Stage 2's embedding-nearest-neighbor lookup. To restore it for a
> given deploy, add the group back to the Dockerfile's `poetry install` line
> and re-add a build-time bake step for the model (see git history for the
> previous version of this Dockerfile stage).

---

## 3. Load data

`migrate up` only creates the schema; the DB is empty until you ingest.

**Production data path — Oracle archives via object storage.** The Oracle
Cloud VM keeps collecting GTFS-RT (~every 30s) and rolls per-day, per-agency
archive zips. Once a day it uploads those zips to S3-compatible object storage
(Cloudflare R2 or AWS S3). The **daily Railway scheduled job** (step 4) pulls
the day's zips over HTTPS and runs `ingest → analyze_all → prune` into the
private `db`. Oracle's dense 30-second archive is why production prefers this
over a live sample — the DB is never exposed and Oracle never accepts inbound
connections.

To kick the first load by hand (the same command the daily job runs), from
your Mac through the app service so it executes on the private network:

```bash
railway run --service app python gtfs_pipeline.py ingest <zips-dir> --agency-id <id>
railway run --service app python gtfs_pipeline.py analyze_all
```

Or just let the daily job (next step) do the first tick.

**Fallback — live fetch (no object storage).** `ingest_live` HTTP-GETs each
agency's `feed_url`. Lower fidelity (it samples the live feed, not the dense
30s archive) but needs no Oracle and no bucket — use it if object storage
isn't wired yet:

```bash
railway run --service app python gtfs_pipeline.py ingest_live
railway run --service app python gtfs_pipeline.py analyze_all
```

Static GTFS (stop names, route polylines) rides along in the archive zips the
job ingests. With the live fallback it isn't fetched — load a static zip once:

```bash
railway run --service app python gtfs_pipeline.py load_static <zip-or-dir> --agency-id <id>
```

Build the Ask router's RAG index once (optional — Ask degrades gracefully
without it, falling through to the LLM). `app`'s own image excludes the
`embeddings` poetry group (see the Image spec note above), so this needs a
one-off install first — still via `railway run --service app` so the
command runs inside the private network that `db`/`clickhouse` are only
reachable from:

```bash
railway run --service app sh -c "pip install --no-cache-dir sentence-transformers && python gtfs_pipeline.py build_rag_index --agency-id 1"
```

---

## 4. Wire the daily ingest job (Railway scheduled service)

> **Which object store? Use Cloudflare R2.** The pattern is write-once /
> read-once-a-day, cross-provider (Railway pulls from the bucket) — so the cost
> that matters is *egress*, and R2 charges **zero egress** (every daily pull and
> backup restore is free; S3 would bill ~$0.09/GB each pull). It's S3-compatible,
> so the `aws s3` commands below work unchanged against its endpoint; storage is
> ~$0.015/GB-mo with a free tier that likely covers early use. Alternative:
> **Backblaze B2** (cheapest storage + egress-free to Cloudflare) if you end up
> retaining many months of raw archives. Avoid plain AWS S3 here (egress cost)
> and Wasabi (90-day minimum-storage charge fights the daily prune). **Set a
> bucket lifecycle rule** to expire old zips (mirror the DB's 400-day prune) so
> storage doesn't grow forever.

Production ingest runs **inside the project**, on the private network, so the
DB stays private (step 1). Add a third service that runs once a day and exits:

1. **+ New → GitHub Repo → the same `transit-app`**. Name it `ingest`.
2. `ingest` → **Settings → Source → Branch**: change it from the default
   (`main`) to **`production`** (see the note at the top of this guide).
3. `ingest` → **Settings → Build**: Dockerfile builder (same image as `app`).
4. `ingest` → **Settings → Deploy → Cron Schedule**: `0 18 * * *`
   (daily; pick an hour after Oracle finishes its upload).
5. `ingest` → **Settings → Deploy → Start Command** — pull the day's zips from
   object storage, then ingest/analyze/prune. Sketch (adjust to your bucket
   client; the image needs an S3 client + `postgresql-client` added to the
   Dockerfile for this service):
   ```bash
   aws s3 sync "s3://$OBJECT_STORE_BUCKET/$(date -u +%F)" /tmp/zips --endpoint-url "$OBJECT_STORE_ENDPOINT"
   for id in $AGENCY_IDS; do
     python gtfs_pipeline.py ingest "/tmp/zips/$id" --agency-id "$id"
   done
   python gtfs_pipeline.py analyze_all
   # retention: aggregates are materialized, so old raw rows can go. analyze
   # full-rebuilds, so history == this window (reports max range is 365d).
   # `updates` lives in ClickHouse, not Postgres — DELETE is a mutation there.
   python -c "
   from pipeline.clickhouse import get_client
   import os
   days = int(os.environ.get('RETENTION_DAYS', 400))
   get_client().command(f'ALTER TABLE updates DELETE WHERE captured_at < now() - INTERVAL {days} DAY')
   "
   ```
6. `ingest` → **Variables**: the same `DATABASE_URL` (private host) plus the
   same `CLICKHOUSE_*` variables as `app` (step 2.4), the `OBJECT_STORE_*`
   creds, and `AGENCY_IDS` / `RETENTION_DAYS` (see `.env.example`).

> **Lock contention in the sketch above is not free to ignore.** `ingest`
> exits `EX_TEMPFAIL` (75) if another ingest/analyze process holds
> `pipeline.locks`' advisory lock (e.g. this job overlapping a manual
> `POST /internal/cron/ingest` poke) — the sketch has no `set -e`, so a
> failed iteration is swallowed and the loop moves to the next `$id`
> automatically, same effect as `scripts/fetch_and_ingest.sh`'s explicit
> `continue` on exit 75. But unlike that script's local replay, this job's
> `/tmp/zips` is on **ephemeral** compute and the bucket pull is
> **date-partitioned** (`$(date -u +%F)`) — a skipped agency isn't
> retried tomorrow, because tomorrow's sync only pulls tomorrow's prefix.
> That day's archive for that agency is gone. Given a lock collision is
> the one failure mode in this loop that's known to be transient, add a
> short bounded retry around each `ingest` call (the zips are still local
> for the rest of this job's run) rather than relying on "next scheduled
> run" to recover it, e.g.:
> ```bash
> for id in $AGENCY_IDS; do
>   for attempt in 1 2 3; do
>     python gtfs_pipeline.py ingest "/tmp/zips/$id" --agency-id "$id" && break
>     code=$?
>     [ "$code" -eq 75 ] || { exit "$code"; }
>     sleep 60
>   done
> done
> ```

> **Fallback path.** If object storage isn't wired yet, the app also exposes
> `POST /internal/cron/ingest` (gated by `CRON_SECRET`), which runs
> `ingest_live` + `analyze` in a background task — poke it from any external
> scheduler. It's the lower-fidelity live-sample path, not the primary one.

> **Continuous freshness (optional, replaces the daily batch's RT lag).**
> Everything above lands RT data once a day. If the Oracle collector VM is
> already running, its pollers can stream every successful protobuf poll
> straight into `app`'s ClickHouse instead — see `oracle_cloud/v3/MIGRATION.md`
> ▸ "9c. Continuous Oracle → application ingest" for the exact
> `COLLECTOR_INGEST_URL`/`COLLECTOR_INGEST_SECRET` wiring. Set
> `COLLECTOR_INGEST_URL` to `app`'s public domain (step 2.5) plus
> `/internal/collector`, generate a shared `COLLECTOR_INGEST_SECRET` on both
> Oracle and `app`, and restart the Oracle poller units. This is additive —
> the daily batch job above still runs as the durable, replayable archive
> path even once streaming is on.

---

## 5. Custom domain (optional)

1. Buy a domain (suggestions in README ▸ Deployment).
2. Railway `app` → **Settings → Networking → Custom Domain** → enter it.
3. Add the CNAME Railway shows you at your registrar. TLS issues
   automatically once DNS resolves.
4. If SSO is on, set `PUBLIC_BASE_URL=https://transit-delay.app` in app
   Variables and add the new callback URIs at Google/GitHub. (The ingest job
   talks to the DB on the private network, so a domain change doesn't touch
   it.)

---

## 6. Updates — promote `main` to `production`

Pushing to `main` does **not** deploy anything — every Railway service
watches `production` (step 1.3 / 2.2 / 4.2 above). A deploy only happens
when you deliberately promote a reviewed `main` commit:

```bash
# 1. Review what's new since the last promotion.
git fetch origin --tags
git diff "$(git describe --tags --match 'deploy-*' --abbrev=0)"..origin/main

# 2. If it looks good, tag it (the durable "reviewed up to here" marker)
#    and promote — this push is what actually triggers the Railway deploy.
TAG="deploy-$(date -u +%Y-%m-%dT%H%M%SZ)"
git tag "$TAG" origin/main
git push origin "$TAG"
git push origin origin/main:production
```

(First-ever promotion: there's no prior `deploy-*` tag yet, so just skip
the `git diff` step and review `main`'s full history, or `git log`, before
tagging and promoting.)

Railway rebuilds from whatever `production` now points to, runs
`migrate up` (pre-deploy), then swaps in the new release once `/health`
passes. The `db` service only redeploys when `db/Dockerfile` itself
changed in that promotion; its volume persists across app deploys.

---

## 7. Backups

Railway has no built-in pg_dump scheduler, and the DB is private — so backups
run **inside the project** too, never over a public connection.

```bash
# one-off / manual, through the private network
railway run --service db pg_dump -U transit transit | gzip > transit-$(date +%F).sql.gz
```

For automation, add a fourth scheduled service (like the `ingest` one in
step 4) whose start command dumps and uploads to the same object storage:

```bash
pg_dump "$DATABASE_URL" | gzip | \
  aws s3 cp - "s3://$OBJECT_STORE_BUCKET/backups/transit-$(date -u +%F).sql.gz" \
  --endpoint-url "$OBJECT_STORE_ENDPOINT"
```

Skip entirely if it's only demo data.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| DB empty after redeploy | Volume not mounted at `/var/lib/postgresql/data` on the `db` service (step 1.6). |
| App healthcheck failing | Deploy Logs — usually `DATABASE_URL` wrong (private host must resolve `${{db.RAILWAY_PRIVATE_DOMAIN}}`, port `5432`) or a missing provider key. |
| `connection refused` to db | `db` service not finished its first boot, or you used the public domain instead of the private one. |
| Migrations didn't run | Confirm `railway.json` `preDeployCommand` is present and the service picked it up (Settings → Deploy). |
| Cron returns 401 | `CRON_SECRET` mismatch between Railway Variables and the GH repo secret. |
| Out of memory at boot | Not the embedder by default — it's excluded from this image (see the Image spec note above). If you've re-added the `embeddings` poetry group and a bake step yourself, that's the likely cause: the e5-small embedder (torch) is heavy (~1–2 GB resident once loaded). Bump the app service's memory, or drop the group back out. |
| Slow first boot / `/health` timeout after a redeploy | Check the deploy's `PUBLISH_IMAGE`/`CREATE_CONTAINER` timing first — a large image takes real time to pull onto the runtime host before the process even starts, independent of anything the app itself does. |
