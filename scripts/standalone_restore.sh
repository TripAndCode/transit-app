#!/usr/bin/env bash
# Restore a backup that scripts/standalone_backup.sh wrote into the standalone
# stack's two databases, then leave the app and poller for `up` to start.
#
#   scripts/standalone_restore.sh <backup directory>
#
# It restores only into empty databases (a new machine, or one whose
# standalone volumes were removed) and refuses when either one holds a table,
# so it never overwrites data. It starts the two databases alone, so the schema
# step cannot create tables first; `up -d app poller` afterwards applies any
# migration newer than the backup.
set -euo pipefail

cd "$(dirname "$0")/.."

dir=${1:?usage: scripts/standalone_restore.sh <backup directory>}
for file in postgres.dump clickhouse.zip; do
  [ -f "$dir/$file" ] || { echo "restore: $dir has no $file" >&2; exit 2; }
done

compose() { docker compose --profile standalone "$@"; }
# Run as the server's own user so the files it writes are ones it can read.
ch() { compose exec -T -u clickhouse standalone-clickhouse "$@"; }
# clickhouse-client takes the user and password from the environment the
# stack gave the container, so the password is never an argument. RESTORE sends
# nothing until it finishes, and the client gives up after receive_timeout
# (300 s by default) while the server carries on.
ch_query() { ch clickhouse-client --receive_timeout=86400 -q "$1"; }

compose up -d --wait standalone-db standalone-clickhouse

# Tables an extension owns (PostGIS's spatial_ref_sys) are not data.
pg_tables=$(compose exec -T standalone-db psql -U transit -d transit -tAc "
  SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')")
ch_tables=$(ch_query "SELECT count() FROM system.tables WHERE database = 'transit'")
if [ "$pg_tables" != 0 ] || [ "$ch_tables" != 0 ]; then
  echo "restore: refused: Postgres holds $pg_tables tables and ClickHouse $ch_tables; restore only into empty databases" >&2
  exit 1
fi

compose exec -T standalone-db pg_restore -U transit -d transit --exit-on-error --single-transaction <"$dir/postgres.dump"

in_ch="/var/lib/clickhouse/backups/restore-$(date -u +%Y%m%dT%H%M%SZ).zip"
# Postgres is restored by now, so a retry would be refused. The trap goes by
# `restored` rather than $?, which a signal leaves at 0.
restored=no
finish() {
  ch rm -f "$in_ch" >/dev/null 2>&1 || true
  [ "$restored" = yes ] ||
    echo "restore: failed after Postgres was restored; remove both database volumes before trying again" \
      "(docs/deploy-standalone.en.md, section 6)" >&2
}
trap finish EXIT
# shellcheck disable=SC2016
ch sh -c 'mkdir -p "$(dirname "$0")" && cat >"$0"' "$in_ch" <"$dir/clickhouse.zip"
ch_query "RESTORE DATABASE transit FROM File('$(basename "$in_ch")')" >/dev/null
restored=yes

echo "restore: done; start the app and poller with: docker compose --profile standalone up -d app poller"
