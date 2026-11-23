#!/usr/bin/env bash
# Back up the standalone stack's two databases while it runs, into a new
# directory under $BACKUP_DIR named for the UTC time: `postgres.dump` (pg_dump's
# custom format) and `clickhouse.zip` (a ClickHouse BACKUP of the database).
# scripts/standalone_restore.sh reads that directory back.
#
# Settings come from the environment, else from `.env` (unquoted values):
#   BACKUP_DIR       where backups go (default ~/transit-backup), outside the
#                    checkout so they are never committed or built into an image
#   BACKUP_KEEP      how many local backups to keep (default 7)
#   BACKUP_PING_URL  a healthchecks.io-style check: pinged when a backup
#                    completes, and at its path plus /fail when one fails
#   BACKUP_STORE_BUCKET, with BACKUP_STORE_ENDPOINT and its key pair: upload
#                    each backup there (scripts/upload_backup.py, run in the
#                    app image). These are read from `.env` alone, the only
#                    place the upload container takes them from.
#
# Run from cron; docs/deploy-standalone.en.md section 6 has the line.
set -euo pipefail

cd "$(dirname "$0")/.."

env_file() { if [ -f .env ]; then sed -n "s/^$1=//p" .env | tail -n 1; fi; }
setting() {
  local value=${!1:-}
  [ -n "$value" ] || value=$(env_file "$1")
  printf '%s' "${value:-$2}"
}

compose() { docker compose --profile standalone "$@"; }
ch() { compose exec -T -u clickhouse standalone-clickhouse "$@"; }
# clickhouse-client takes the user and password from the environment the
# stack gave the container, so the password is never an argument.
ch_query() { ch clickhouse-client -q "$1"; }

backup_root=$(setting BACKUP_DIR "$HOME/transit-backup")
keep=$(setting BACKUP_KEEP 7)
ping_url=$(setting BACKUP_PING_URL "")
bucket=$(env_file BACKUP_STORE_BUCKET)
# Read in base 10: the shell's arithmetic takes a leading zero for octal.
case $keep in '' | *[!0-9]*) keep=0 ;; *) keep=$((10#$keep)) ;; esac
[ "$keep" -gt 0 ] || { echo "backup: BACKUP_KEEP must be a positive whole number" >&2; exit 2; }
# `.env` is read as text, so a `~` there is not the shell's; a relative path
# would land inside the checkout.
# shellcheck disable=SC2088
case $backup_root in
  "~/"*) backup_root="$HOME/${backup_root#"~/"}" ;;
  /*) ;;
  *) echo "backup: BACKUP_DIR must be an absolute path" >&2; exit 2 ;;
esac

stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir="$backup_root/$stamp"
# ClickHouse's default config allows BACKUP to File() only under the server's
# own backups directory, which lives on the database volume.
in_ch="/var/lib/clickhouse/backups/$stamp.zip"

# Never prints the URL: its path is the check's credential.
report() {
  [ -n "$ping_url" ] || return 0
  local base=${ping_url%%\?*}
  local query=${ping_url#"$base"}
  local target=$ping_url
  [ "$1" = fail ] && target="${base%/}/fail$query"
  curl -fsS -m 10 --retry 2 -o /dev/null --data-binary "$2" "$target" ||
    echo "backup: the ping could not be sent" >&2
}

# A backup that failed before both files were written is removed, so pruning
# never counts it; one that failed only to upload stays. Only a directory this
# run made is removed: a second run in the same second finds it taken.
made=no
kept=no
finish() {
  local status=$?
  [ "$status" -eq 0 ] && return
  if [ "$made" = yes ] && [ "$kept" = no ]; then
    rm -rf -- "$dir"
    ch rm -f "$in_ch" >/dev/null 2>&1 || true
  fi
  echo "backup: failed (local copy kept: $kept)" >&2
  report fail "backup $stamp failed"
}
trap finish EXIT

# The dumps hold every row, so only their owner can read them.
umask 077
mkdir -p "$backup_root"
mkdir "$dir"
made=yes

compose exec -T standalone-db pg_dump -U transit -d transit -Fc >"$dir/postgres.dump"

ch_query "BACKUP DATABASE transit TO File('$stamp.zip')" >/dev/null
ch cat "$in_ch" >"$dir/clickhouse.zip"
kept=yes

# From here a failure leaves the backup in place: the run still prunes, then
# reports the failure.
complete=yes
ch rm -f "$in_ch" || { echo "backup: could not remove ClickHouse's copy at $in_ch" >&2; complete=no; }

# The upload runs as this user, the only one who can read the backup; on a
# Linux host the image's own user is another uid.
if [ -n "$bucket" ]; then
  compose run --rm --no-deps -T -u "$(id -u):$(id -g)" -v "$dir:/backup:ro" app \
    python scripts/upload_backup.py /backup "$stamp" || complete=no
fi

# Only directories this script names are pruned, newest kept. Pruning runs
# whether or not the upload worked, so a broken upload cannot fill the disk.
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z' |
  sort -r | tail -n +"$((keep + 1))" | while IFS= read -r old; do rm -rf -- "$old"; done
[ "$complete" = yes ] || exit 1

echo "backup: $dir"
report ok "backup $stamp"
