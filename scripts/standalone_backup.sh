#!/usr/bin/env bash
# Back up the standalone stack's two databases while it runs, into a new
# directory under $BACKUP_DIR named for the UTC time: `postgres.dump` (pg_dump's
# custom format) and `clickhouse.zip` (a ClickHouse BACKUP of the database).
# scripts/standalone_restore.sh reads that directory back.
#
# Settings come from the environment, else from `.env` (unquoted values):
#   BACKUP_DIR       where backups go (default ~/transit-backup), outside the
#                    checkout so they are never committed or built into an image
#   BACKUP_KEEP      how many complete local backups to keep (default 7)
#   BACKUP_PING_URL  a healthchecks.io-style check: pinged when a backup
#                    completes, and at its path plus /fail when one fails
#   BACKUP_STORE_BUCKET, with BACKUP_STORE_ENDPOINT and its key pair: upload
#                    each backup there (scripts/upload_backup.py, run in the
#                    app image). These are read from `.env` alone, the only
#                    place the upload container takes them from.
#
# Run from cron; docs/deploy-standalone.en.md section 6 has the line.
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,/^set /{/^set /!p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac

cd "$(dirname "$0")/.."

# Read as Compose reads an unquoted value, since the stack reads the same file:
# whitespace then `#` starts a comment.
env_file() { if [ -f .env ]; then sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/[[:space:]]#.*//; s/[[:space:]]*$//'; fi; }
setting() {
  local value=${!1:-}
  [ -n "$value" ] || value=$(env_file "$1")
  printf '%s' "${value:-$2}"
}

compose() { docker compose --profile standalone "$@"; }
ch() { compose exec -T -u clickhouse standalone-clickhouse "$@"; }
# clickhouse-client takes the user and password from the environment the
# stack gave the container, so the password is never an argument. BACKUP sends
# nothing until it finishes, and the client gives up after receive_timeout
# (300 s by default) while the server carries on.
ch_query() { ch clickhouse-client --receive_timeout=86400 -q "$1"; }

backup_root=$(setting BACKUP_DIR "$HOME/transit-backup")
keep=$(setting BACKUP_KEEP 7)
ping_url=$(setting BACKUP_PING_URL "")
bucket=$(env_file BACKUP_STORE_BUCKET)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
stamp_glob='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z'
# ClickHouse's default config allows BACKUP to File() only under the server's
# own backups directory, which lives on the database volume.
ch_backups=/var/lib/clickhouse/backups
in_ch="$ch_backups/$stamp.zip"

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

# The trap goes by `succeeded`, set on the last line, not by $?: a signal (the
# host shutting down, say) leaves $? at 0 inside the trap. A run that fails
# before both files are written removes its directory; one that fails only to
# upload, or to remove ClickHouse's copy, keeps it. Only a directory this run
# made is removed: a second run in the same second finds it taken.
succeeded=no
made=no
kept=no
finish() {
  [ "$succeeded" = yes ] && return
  # errexit still holds inside a trap, and no cleanup step may stop the ping.
  set +e
  if [ "$made" = yes ] && [ "$kept" = no ]; then
    rm -rf -- "$dir"
    ch rm -f "$in_ch" >/dev/null 2>&1 || true
  fi
  echo "backup: failed (local copy kept: $kept)" >&2
  report fail "backup $stamp failed"
}
trap finish EXIT

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
dir="$backup_root/$stamp"

# The dumps hold every row, so only their owner can read them.
umask 077
mkdir -p "$backup_root"
mkdir "$dir"
made=yes

# Each file takes its real name only once both are written, so a run cut off
# where no trap runs (SIGKILL, power loss) leaves nothing that pruning or a
# restore takes for a backup.
compose exec -T standalone-db pg_dump -U transit -d transit -Fc >"$dir/postgres.dump.part"

# Such a run can also leave its copy in ClickHouse's backups directory, which
# the next BACKUP needs the room of. One younger than half a day may belong to a
# run still going.
ch mkdir -p "$ch_backups"
ch find "$ch_backups" -maxdepth 1 -type f -name "$stamp_glob.zip" -mmin +720 -delete
ch_query "BACKUP DATABASE transit TO File('$stamp.zip')" >/dev/null
ch cat "$in_ch" >"$dir/clickhouse.zip.part"
mv -- "$dir/postgres.dump.part" "$dir/postgres.dump"
mv -- "$dir/clickhouse.zip.part" "$dir/clickhouse.zip"
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

# Pruning keeps the newest $keep backups holding both files, and removes every
# directory this script names that is older than the last of them. One without
# both files does not count, and goes once it is older than every kept backup.
# It runs whether or not the upload worked, so a broken upload cannot fill the
# disk.
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name "$stamp_glob" | sort -r | {
  counted=0
  while IFS= read -r old; do
    if [ "$counted" -ge "$keep" ]; then
      rm -rf -- "$old"
    elif [ -f "$old/postgres.dump" ] && [ -f "$old/clickhouse.zip" ]; then
      counted=$((counted + 1))
    fi
  done
}
[ "$complete" = yes ] || exit 1

echo "backup: $dir"
succeeded=yes
report ok "backup $stamp"
