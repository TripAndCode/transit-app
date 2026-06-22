# Deploy to Railway (push-to-deploy)

Lowest-effort path: Railway runs your **existing Docker images** — the app
(`Dockerfile`, with the SPA baked in) and the custom PostGIS+pgvector DB
(`db/Dockerfile`) — on private networking, with free TLS and a managed
domain. No box to harden, no Caddy, no SSH. Push to your default branch →
Railway rebuilds and redeploys.

Cost: ~$10–18/mo usage-based, in exchange for zero server ops and git-push
deploys — pick this if you'd rather not manage a Linux box yourself.

> **Why not a managed Postgres add-on?** Migration `0001` does
> `CREATE EXTENSION postgis / vector`, and `0012` adds `pg_trgm`. Render's
> managed PG supports all three, but the e5-small embedder (torch, ~1–2 GB
> resident) would then force a 2 GB app instance *plus* a separate paid DB
> — ~$32/mo, over budget. Running our own `db/Dockerfile` on Railway keeps
> app + DB on one usage bill and sidesteps any "does the platform have my
> extension" question entirely.

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
3. `db` service → **Settings → Build**:
   - Builder: **Dockerfile**
   - Dockerfile Path: `db/Dockerfile`
4. `db` → **Variables** (these feed the `postgis/postgis` image):
   ```
   POSTGRES_USER=transit
   POSTGRES_PASSWORD=<openssl rand -hex 24>
   POSTGRES_DB=transit
   ```
5. `db` → **Settings → Volumes → + Volume**, mount path:
   ```
   /var/lib/postgresql/data
   ```
   **Without this the database is wiped on every redeploy.** This is the
   one stateful piece of the whole deploy.
6. Deploy `db`. Wait until it's running. Note its private hostname under
   **Settings → Networking → Private Networking**: `db.railway.internal`.

> Postgres is reachable **only** on the private network (`*.railway.internal`,
> port 5432). Don't add a public TCP proxy to it — the app talks to it
> internally, same as the compose `db` service. The daily ingest + backup
> jobs (steps 4 and 7) also run **inside** the project, so nothing external
> ever needs to reach the DB.

---

## 2. Create the app service

1. **+ New → GitHub Repo → the same `transit-app`**. Name it `app`.
2. Railway auto-detects `railway.json` → Dockerfile builder + `/health`
   healthcheck + the `migrate up` pre-deploy command. Nothing to configure.
3. `app` → **Variables**:
   ```
   DATABASE_URL=postgresql://transit:<the POSTGRES_PASSWORD from step 1.4>@db.railway.internal:5432/transit
   GROQ_API_KEY=gsk_...
   CRON_SECRET=<openssl rand -hex 32>      # save this — it must match the GH secret (step 4)
   CHAT_PROVIDERS=cerebras,groq            # add CEREBRAS_API_KEY too if using it
   CEREBRAS_API_KEY=...                    # optional but recommended (primary rung)
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
4. `app` → **Settings → Networking → Generate Domain**. Railway issues
   `https://<something>.up.railway.app` with TLS. Copy it — that's
   `APP_BASE_URL` for the cron and `PUBLIC_BASE_URL` for SSO.
5. Deploy. The pre-deploy `migrate up` runs first; watch **Deploy Logs**
   for `applied migration 0001 … 0016`, then the uvicorn boot line.

Smoke test:

```bash
curl -fsS https://<your>.up.railway.app/health      # → 200
```

Open the domain in a browser — SPA loads. Tabs are empty until data lands
(next step), and Ask works once `GROQ_API_KEY`/`CEREBRAS_API_KEY` is valid.

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
without it, falling through to the LLM):

```bash
railway run --service app python gtfs_pipeline.py build_rag_index --agency-id 1
```

---

## 4. Wire the daily ingest job (Railway scheduled service)

Production ingest runs **inside the project**, on the private network, so the
DB stays private (step 1). Add a third service that runs once a day and exits:

1. **+ New → GitHub Repo → the same `transit-app`**. Name it `ingest`.
2. `ingest` → **Settings → Build**: Dockerfile builder (same image as `app`).
3. `ingest` → **Settings → Deploy → Cron Schedule**: `0 18 * * *`
   (daily; pick an hour after Oracle finishes its upload).
4. `ingest` → **Settings → Deploy → Start Command** — pull the day's zips from
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
   psql "$DATABASE_URL" -c "DELETE FROM updates WHERE captured_at < now() - interval '${RETENTION_DAYS:-400} days'"
   ```
5. `ingest` → **Variables**: the same `DATABASE_URL` (private host) plus the
   `OBJECT_STORE_*` creds and `AGENCY_IDS` / `RETENTION_DAYS` (see `.env.example`).

> **Fallback path.** If object storage isn't wired yet, the app also exposes
> `POST /internal/cron/ingest` (gated by `CRON_SECRET`), which runs
> `ingest_live` + `analyze` in a background task — poke it from any external
> scheduler. It's the lower-fidelity live-sample path, not the primary one.

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

## 6. Updates

Just push:

```bash
git push origin main
```

Railway rebuilds the app, runs `migrate up` (pre-deploy), then swaps in the
new release once `/health` passes. New migrations apply automatically — no
manual step, unlike the Linode path. The `db` service only redeploys when
`db/Dockerfile` itself changes; its volume persists across app deploys.

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
| DB empty after redeploy | Volume not mounted at `/var/lib/postgresql/data` on the `db` service (step 1.5). |
| App healthcheck failing | Deploy Logs — usually `DATABASE_URL` wrong (private host must be `db.railway.internal`, port `5432`) or a missing provider key. |
| `connection refused` to db | `db` service not finished its first boot, or you used the public domain instead of `*.railway.internal`. |
| Migrations didn't run | Confirm `railway.json` `preDeployCommand` is present and the service picked it up (Settings → Deploy). |
| Cron returns 401 | `CRON_SECRET` mismatch between Railway Variables and the GH repo secret. |
| Out of memory at boot | The e5-small embedder (torch) is heavy. Bump the app service's memory, or set `ASK_ROUTER_ENABLED=false` to skip loading it (Ask falls through to the LLM — Stages 1 & 3 still work). |
