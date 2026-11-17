# Deploy for your city: one machine, your own feeds

[日本語](deploy-standalone.md)

One machine with Docker runs the whole app for the bus feeds you choose. It needs no cloud account, no object storage and no LLM key. Readers need no account either.

## What you need

- Docker with the Compose plugin, and `git`. Any Linux, macOS or Windows (WSL) host will do.
- Outbound HTTPS to your feed and timetable URLs. Nothing needs to reach the machine from outside.
- For each agency, two public URLs: its **GTFS-RT TripUpdates** feed and its **GTFS (GTFS-JP) static zip**. [`feeds/`](../feeds/README.md) lists feeds already known to work.

## 1. Clone and configure

```bash
git clone https://github.com/TripAndCode/transit-app.git && cd transit-app
cp .env.example .env
```

The stack runs with `.env` as it is. Edit these only if you need them:

| Variable | When |
| --- | --- |
| `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD` | To sign in as an administrator (step 4). Uncomment both. |
| `SESSION_SIGNING_KEY` | Required once either sign-in method is on: `openssl rand -hex 32`. |
| `POLL_INTERVAL_SEC` | Seconds between polls (default 60). |
| `STANDALONE_PORT` | Host port for the app (default 8000, bound to `127.0.0.1`). |
| `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD` | Change from `transit` if others can reach the machine's Docker network. Set them before the first start: the databases keep the password they were created with. |

Ignore the Oracle, R2, weather and LLM settings. `GEMINI_API_KEY` and `OPENAI_API_KEY` stay empty: the Ask page's model-backed answers are then off and everything else works.

## 2. Start

```bash
docker compose --profile standalone up -d --build app poller
```

Naming `app poller` starts only the standalone stack: its own Postgres and ClickHouse (no host port, their own volumes), the schema step, the app and the poller. Do not use a bare `--profile standalone up`: it also starts the two development databases. The first build takes several minutes. Then open <http://localhost:8000>; the page loads but shows no agency yet.

## 3. Add an agency

```bash
docker compose --profile standalone exec app python gtfs_pipeline.py add_agency \
  --feed-url https://example.test/trip_updates.pb --static-url https://example.test/gtfs.zip
```

Add `--dry-run` first to see the verdict and write nothing. The command downloads the timetable, polls the feed for 120 seconds (`--probe-seconds`) and checks that the feed's StopTimeUpdates join the timetable by `(trip_id, stop_sequence)`. Quote the URLs in a shell if they contain `&`.

Read the last lines of its output. On success it prints `verdict: accept`, the agency name and `agency_id`, and has loaded the timetable. Any other verdict adds nothing:

| Verdict | Meaning and what to do |
| --- | --- |
| `accept` | At least 80% of StopTimeUpdates joined. Added with `static_join`. |
| `accept_empty` | Only with `--accept-empty`: the feed sent nothing during the probe (outside service hours). Added unchecked. |
| `name_required` | `agency.txt` lists several agencies or none by name: repeat with `--name "..."`. |
| `feed_taken` | The feed URL is already an agency. |
| `url_rejected` | The URL is not http(s) or points at a private address. |
| `static_unavailable`, `static_unreadable`, `static_incomplete` | The zip cannot be downloaded, cannot be read, or lacks `trips.txt`, `stops.txt` or `stop_times.txt` rows. |
| `feed_unreachable`, `not_a_feed`, `empty` | The feed does not answer, is not GTFS-RT, or sent no entities. Retry during service hours. |
| `no_trip_updates`, `no_trip_ids`, `no_stop_time_updates` | The feed has no TripUpdates, or they carry no `trip_id` or no stop times. |
| `stop_id_only` | StopTimeUpdates name stops but no `stop_sequence`, so they cannot join. |
| `low_join` | Under 80% joined: the timetable and the feed disagree. The output quotes sample misses. |

The exact reason follows each verdict in the output. If you pass `--dry-run`, run again without it to add the agency. A refusal writes nothing, and the poller never retries it: fix the cause, then run the command again.

To set the columns yourself (another strategy, a `trip_id_pattern`, a fixed `agency_id`), write a CSV with the columns of `agencies.csv`, copy it in with `docker compose --profile standalone cp my_agency.csv app:/tmp/my_agency.csv`, then run `seed_agencies /tmp/my_agency.csv` and `refresh-static` the same way as `add_agency` above. This skips the probe.

## 4. Sign in as administrator

Readers need no sign-in. Administrators use `/login` to reach the admin pages (the Agencies page edits strategies). Set `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD` and `SESSION_SIGNING_KEY` in `.env`, then `docker compose --profile standalone up -d app`. The Google and GitHub alternative (`ADMIN_EMAILS` and the client IDs) is described in `.env.example`.

## 5. When data appears

The poller reads the agency list every cycle, so the agency is picked up within a minute of being added, and Pulse shows today's data from the polls that follow. Allow the first few polls, not hours. A feed outside service hours has nothing to show until its first trips run.

A closed day becomes history at the next JST midnight. After the UTC day has closed, the poller packs the day's raw polls and replaces the day with a re-ingest of them; this runs by itself.

Check progress with `docker compose --profile standalone logs -f poller`.

## 6. Back up

Three named volumes hold everything: `standalone_pgdata` (agencies, timetables, aggregates), `standalone_chdata` (the polls as data) and `standalone_archive` (the raw polls, one tarball per agency per day). Compose prefixes each with the project name, which is the checkout's directory name.

```bash
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -q standalone-db))
docker compose --profile standalone stop app poller standalone-clickhouse standalone-db
mkdir -p backup
for v in $(docker volume ls -q --filter label=com.docker.compose.project=$P --filter name=standalone_); do
  docker run --rm -v "$v":/data:ro -v "$PWD/backup":/backup alpine tar czf "/backup/$v.tgz" -C /data .
done
docker compose --profile standalone up -d app poller
```

`P` is this checkout's Compose project, so the loop reaches only its three volumes. The poller records nothing while stopped; a short stop is a gap of that length. Copy `backup/` off the machine. The archive volume is never pruned: it grows by one tarball per agency per day.

## 7. Upgrade

```bash
git pull
docker compose --profile standalone up -d --build app poller
```

The schema step (`gtfs_pipeline.py schema-up`) runs before the app and poller restart, so migrations apply themselves. Back up first when a release changes the schema. A migration that would lose data on a database whose history has been pruned makes the schema step refuse: `up` then reports the failure and `app` does not start, because it waits for that step. Read the reason in `docker compose --profile standalone logs migrate`. After a backup, name the migration version in `MIGRATE_ALLOW_DESTRUCTIVE_UP` in `.env` and run the `up` command again.

## What is not automated

- A timetable the agency republishes: run `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static` again.
- The weather and ML forecast passes the Oracle cron job runs.
- Uploading tarballs or backups anywhere off the machine, and pruning the archive.
- TLS and a public hostname: the app listens on `127.0.0.1` only. Put your own reverse proxy in front, and set `PUBLIC_BASE_URL` and `FORWARDED_HOPS` for it.
- If ingest falls behind, the poller log names the tarballs a run reads. To ingest an agency's whole archive by hand: `docker compose --profile standalone exec poller python gtfs_pipeline.py ingest /app/archive/<id>/rt --agency-id <id>`.
