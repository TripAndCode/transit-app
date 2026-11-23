# Deploy for your city: run it on one machine

[日本語](deploy-standalone.md)

One machine with Docker runs the whole app for the bus feeds you choose. It needs no cloud account, no object storage and no LLM key. Readers need no account either.

## What you need

- Docker with the Compose plugin, and `git`. Any Linux, macOS or Windows (WSL) host will do.
- Outbound HTTPS to your feed and timetable URLs. Nothing needs to reach the machine from outside.
- For each agency, two public URLs: its **GTFS-RT TripUpdates** feed and its **GTFS (GTFS-JP) static zip**. [`feeds/`](../feeds/README.en.md) lists known feeds: a row whose verdict is `accept` passed the probe on its date. Use a row's `realtime_url` as `--feed-url` and its `static_url` as `--static-url` in step 3.

## 1. Clone and configure

```bash
git clone https://github.com/TripAndCode/transit-app.git && cd transit-app
cp .env.example .env
```

The stack runs with `.env` as it is. Edit these only if you need them:

| Variable | When |
| --- | --- |
| `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`, `SESSION_SIGNING_KEY` | To sign in as an administrator (step 4). Set all three (uncomment the two admin lines); the key comes from `openssl rand -hex 32`. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ADMIN_EMAILS`, `SESSION_SIGNING_KEY` | The alternative to the local administrator: Google and GitHub sign-in needs all four client variables and the key, and `ADMIN_EMAILS` must list the administrators' email addresses: otherwise every OAuth sign-in is refused. `.env.example` explains them. |
| `POLL_INTERVAL_SEC` | Seconds between polls (default 60). |
| `STANDALONE_PORT`, `PUBLIC_BASE_URL` | To use a host port other than 8000 (it is bound to `127.0.0.1`), set both: `STANDALONE_PORT=9000` and `PUBLIC_BASE_URL=http://localhost:9000`. Sign-in only accepts requests from the origin `PUBLIC_BASE_URL` names. |
| `FORWARDED_HOPS` | The number of reverse proxies in front of the app (default 0). See "What is not automated". |
| `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD` | Change from `transit` if others can reach the machine's Docker network. Set them before the first start: the databases keep the password they were created with. |

Ignore the Oracle, R2, weather and LLM settings. `GEMINI_API_KEY` and `OPENAI_API_KEY` stay empty: the Ask page's model-backed answers are then off and everything else works.

## 2. Start

```bash
docker compose --profile standalone up -d --build app poller
```

Naming `app poller` starts only the standalone stack: its own Postgres and ClickHouse (no host port, their own volumes), the schema step, the app and the poller. Do not use a bare `--profile standalone up`: it also starts the two development databases. The first build takes several minutes. Then open the URL `PUBLIC_BASE_URL` names (<http://localhost:8000> by default); the page loads but shows no agency yet.

## 3. Add an agency

```bash
docker compose --profile standalone exec app python gtfs_pipeline.py add_agency \
  --feed-url 'https://example.test/trip_updates.pb' --static-url 'https://example.test/gtfs.zip'
```

Add `--dry-run` first to see the verdict and write nothing. The command downloads the timetable, polls the feed for 120 seconds (`--probe-seconds`) and checks that the feed's StopTimeUpdates join the timetable by `(trip_id, stop_sequence)`. Keep the quotes: URLs often contain `&`.

Read the last lines of its output. On success it prints `verdict: accept`, the agency name and `agency_id`, and has loaded the timetable. Every verdict except `accept` and `accept_empty` adds nothing:

| Verdict | Meaning and what to do |
| --- | --- |
| `accept` | At least 80% of StopTimeUpdates joined. Added with `static_join`. |
| `accept_empty` | Only with `--accept-empty`: the feed sent nothing during the probe (outside service hours). Added unchecked. |
| `name_required` | `agency.txt` lists several agencies or none by name: repeat with `--name "..."`. |
| `feed_taken` | The feed URL is already an agency. |
| `url_rejected` | The URL is not http(s), points at a private address, or its host does not resolve. |
| `static_unavailable`, `static_unreadable`, `static_incomplete` | The zip cannot be downloaded, cannot be read, or lacks `trips.txt`, `stops.txt` or `stop_times.txt` rows. |
| `feed_unreachable`, `not_a_feed`, `empty` | The feed does not answer, is not GTFS-RT, or sent no entities. Retry during service hours. |
| `no_trip_updates`, `no_trip_ids`, `no_stop_time_updates` | The feed has no TripUpdates, or they carry no `trip_id` or no stop times. |
| `stop_id_only` | StopTimeUpdates name stops but no `stop_sequence`, so they cannot join. |
| `low_join` | Under 80% joined: the timetable and the feed disagree. The output quotes sample misses. |

The exact reason follows each verdict in the output. A refusal writes nothing, and the poller never retries it: fix the cause, then run the command again. If you passed `--dry-run`, run it again without to add the agency. If the output says nothing was added because the stores cannot take the load, check `docker compose --profile standalone logs migrate` and run the command again. If the output says the agency was added but its timetable did not load, run `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static --agency-id <id>`.

To set the columns yourself (another strategy, a `trip_id_pattern`, a fixed `agency_id`), write a CSV with the columns of `agencies.csv`, copy it in with `docker compose --profile standalone cp my_agency.csv app:/tmp/my_agency.csv`, then run `seed_agencies /tmp/my_agency.csv` and `refresh-static` the same way as `add_agency` above. This skips the probe.

## 4. Sign in as administrator

Readers need no sign-in. Administrators use `/login` to reach the admin pages (the Agencies page edits strategies). For the local administrator, set `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD` and a real `SESSION_SIGNING_KEY` in `.env` (all three: without the key the app refuses to start, and `docker compose --profile standalone logs app` shows why), then run `docker compose --profile standalone up -d app`. The Google and GitHub alternative is in `.env.example`.

## 5. When data appears

The poller reads the agency list every cycle, so it picks the agency up within a minute of being added. The Live view (live delays) shows today's trips within a few minutes of `add_agency`.

Pulse and Reports read daily aggregates, so they show a day only after it closes: after the first JST midnight following the first poll. Until then they show 0 samples, which is expected. The archive then replaces that day once the UTC day closes. A feed outside service hours has nothing to show until its first trips run.

Check progress with `docker compose --profile standalone logs -f poller`.

## 6. Back up

`scripts/standalone_backup.sh` backs up both databases while the stack runs: Postgres with `pg_dump`, and ClickHouse with its own `BACKUP`. Each run writes a new directory under `BACKUP_DIR`, named for the UTC time, and keeps the newest `BACKUP_KEEP`. Run it nightly from the host's crontab (`crontab -e`):

```cron
15 3 * * * cd /path/to/transit-app && PATH=/usr/local/bin:/usr/bin:/bin scripts/standalone_backup.sh >>"$HOME/transit-backup.log" 2>&1
```

cron's own `PATH` can lack Docker, so the `PATH=` there must include the directory that `command -v docker` prints.

The script reads `BACKUP_DIR`, `BACKUP_KEEP` and `BACKUP_PING_URL` from the environment, else from `.env`. The `BACKUP_STORE_*` settings go in `.env` itself, because the upload runs in a container that reads only that file. Write the values there without quotes.

| Setting | What it does |
| --- | --- |
| `BACKUP_DIR` | Where backups go, as an absolute path or one starting `~/`. Default `~/transit-backup`. Keep it outside the checkout, so backups are never committed or built into an image. |
| `BACKUP_KEEP` | How many backups stay on this machine. Default 7. |
| `BACKUP_PING_URL` | A [healthchecks.io](https://healthchecks.io)-style check with a period of 1 day and a grace time of 2 hours. It is pinged when a backup completes, and at `<url>/fail` when one fails, so a failed or missed night alerts. |
| `BACKUP_STORE_ENDPOINT`, `BACKUP_STORE_BUCKET`, `BACKUP_STORE_ACCESS_KEY_ID`, `BACKUP_STORE_SECRET_ACCESS_KEY` | Also upload each backup to S3-compatible storage, such as Cloudflare R2, under `<BACKUP_STORE_PREFIX>/<time>/` (prefix default `backups`). The key needs write access to that bucket. Set the bucket's own lifecycle rule to delete old backups there. A failed upload keeps the local copy and pings `/fail`. |

ClickHouse writes its backup onto its own volume before the script copies it out, so while it runs that disk needs room for one more backup.

Each backup holds the whole of both databases, so it grows with the history. It leaves out the archive volume (the raw polls), whose content ClickHouse already holds once ingested. To keep the archive too, or to copy the whole machine, stop the stack and copy its three volumes:

```bash
B=${BACKUP_DIR:-$HOME/transit-backup}
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -a -q standalone-db))
docker compose --profile standalone stop app poller standalone-clickhouse standalone-db
mkdir -p "$B"
for v in $(docker volume ls -q --filter label=com.docker.compose.project=$P --filter name=standalone_); do
  docker run --rm -v "$v":/data:ro -v "$B":/backup alpine tar czf "/backup/$v.tgz" -C /data .
done
docker compose --profile standalone up -d app poller
```

`P` is this checkout's Compose project, so the loop reaches only its three volumes. The poller records nothing while stopped; a short stop is a gap of that length. The archive volume is never pruned: it grows by one tarball per agency per day.

### Restore

A backup restores only into empty databases: on a new machine, or after this checkout's standalone volumes are removed. The script refuses when either database already holds a table, so it never overwrites data. From the checkout:

```bash
scripts/standalone_restore.sh ~/transit-backup/20260101T031500Z
docker compose --profile standalone up -d app poller
```

The script starts only the two databases and restores both. `up` then runs the schema step, which applies any migration newer than the backup, and starts the app and poller. To restore from the bucket, first download one `<prefix>/<time>/` folder into a local directory with your storage provider's tools. Polls between the backup and the restore are missing.

If a restore fails partway, Postgres may already hold the backup, and the script then refuses to run again. Remove the two database volumes, which leaves the archive, and run it again:

```bash
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -a -q standalone-db))
docker compose --profile standalone down
docker volume rm "${P}_standalone_pgdata" "${P}_standalone_chdata"
```

## 7. Upgrade

```bash
git pull
docker compose --profile standalone up -d --build app poller
```

The schema step (`gtfs_pipeline.py schema-up`) runs before the app and poller restart, so migrations apply themselves. Back up first when a release changes the schema. A migration that would delete stored data on a database that has already discarded old history makes the schema step refuse: `up` then reports the failure and `app` does not start, because it waits for that step. Read the reason in `docker compose --profile standalone logs migrate`. After a backup, name the migration version in `MIGRATE_ALLOW_DESTRUCTIVE_UP` in `.env` and run the `up` command again.

## 8. Monitor the daily step

The poller can report its daily step to a [healthchecks.io](https://healthchecks.io) check, or to any service that takes the same pings. That step moves each closed day into history and the reports, so when it stops, the reports stop advancing.

1. Create a check with a period of 1 day and a grace time of 2 hours.
2. Put its ping URL in `.env` as `POLLER_PING_URL=<url>`, then restart the poller: `docker compose --profile standalone up -d poller`.
3. The poller runs a step right after its first poll cycle, and the check records a ping when that step ends with no stage failing. A first cycle that archives no poll at all (no agency yet, or every feed failing) runs no step, and the first one follows the next UTC midnight. If none arrives, the poller's log says why: a failing stage, or a ping it could not send. The log never prints the URL.

The poller pings the URL each time a step ends with no stage failing: normally once after JST midnight and once after UTC midnight (09:00 JST). When a stage has failed every retry, about two hours of them, it pings `<url>/fail` and the check alerts at once. An agency with no timetable loaded skips its ingest, which no retry heals, so that step pings `<url>/fail` at once: load the timetable with `refresh-static` (section 3). When the poller stops, or a step never ends, the pings stop and the check alerts 26 hours after the last one. After a failure, the check comes back up once a later day's step ends with no stage failing.

## What is not automated

- A timetable the agency republishes: run `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static` again.
- The weather and forecast passes that the full deployment's scheduled jobs run.
- Copying the archive's tarballs off the machine, and pruning the archive.
- TLS and a public hostname: the app listens on `127.0.0.1` only. Put your own reverse proxy in front, and set `PUBLIC_BASE_URL` to its public address and `FORWARDED_HOPS` to the number of proxies, in `.env`.

## Troubleshooting

- `docker compose --profile standalone ps -a` shows whether `app` and `poller` are running or restarting.
- If `app` or `poller` keeps restarting, or does not start, read `docker compose --profile standalone logs app` (or `poller`). A missing or default `SESSION_SIGNING_KEY` shows up there.
- If ingest falls behind, the poller log names the tarballs a run reads. To ingest an agency's whole archive by hand: `docker compose --profile standalone exec poller python gtfs_pipeline.py ingest /app/archive/<id>/rt --agency-id <id>`.
